"""Flask application and entry point for interactive pipeline exploration."""

from __future__ import annotations

import os
import copy
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory

from actor import Actor
from arbiter import Arbiter
from bbox_generator import BBoxGenerator
from gatekeeper import Gatekeeper
from pipeline import EndToEndPipeline
from utils.output_store import output_data_path

from .actor_data import actor_view, list_episode_ids
from .arbiter_data import arbiter_view, list_arbiter_episode_ids
from .bbox_generation_data import bbox_generation_view, list_bbox_generation_episode_ids
from .gatekeeper_data import gatekeeper_view, list_gatekeeper_episode_ids
from .pipeline_data import (
    initial_stages, new_pipeline_output, pipeline_sample, pipeline_selection, pipeline_stage_views,
)
from utils.configuration import (
    load_dotenv,
    provider_for_role,
    repository_root,
    startup_error_message,
    validate_startup,
)


_TABS = (
    ("pipeline", "End-to-End Pipeline"),
    ("actor", "Actor Test"),
    ("arbiter", "Arbiter Test"),
    ("gatekeeper", "Gatekeeper Test"),
    ("bbox-generation", "Bounding Box Generation Test"),
)


def create_app() -> Flask:
    """Create the web UI; startup configuration is checked by ``main``."""

    app = Flask(__name__)
    # Keep local material development responsive to template edits without
    # enabling Flask's debug mode or its process reloader.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True
    actor_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="actor-request")
    actor_jobs: dict[str, dict[str, Any]] = {}
    actor_jobs_lock = threading.Lock()
    arbiter_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="arbiter-request")
    arbiter_jobs: dict[str, dict[str, Any]] = {}
    arbiter_jobs_lock = threading.Lock()
    gatekeeper_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gatekeeper-request")
    gatekeeper_jobs: dict[str, dict[str, Any]] = {}
    gatekeeper_jobs_lock = threading.Lock()
    bbox_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bbox-request")
    bbox_jobs: dict[str, dict[str, Any]] = {}
    bbox_jobs_lock = threading.Lock()
    pipeline_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline-run")
    pipeline_jobs: dict[str, dict[str, Any]] = {}
    pipeline_jobs_lock = threading.Lock()

    @app.before_request
    def protect_running_pipeline():
        if request.method != "POST" or request.path not in {
            "/api/actor/run", "/api/arbiter/run", "/api/gatekeeper/run", "/api/bbox-generation/run",
        }:
            return None
        payload = request.get_json(silent=True) or {}
        with pipeline_jobs_lock:
            busy = any(job["status"] in {"queued", "running"}
                       and all(payload.get(field) == job[field] for field in ("episode_id", "output_id", "step_index"))
                       for job in pipeline_jobs.values())
        if busy:
            return jsonify({"error": "This step is running in the end-to-end pipeline. Wait until it finishes."}), 409

    def run_pipeline_job(job_id: str) -> None:
        with pipeline_jobs_lock:
            job = pipeline_jobs[job_id]
            episode_id, output_id, step_index = job["episode_id"], job["output_id"], job["step_index"]
            job["status"] = "running"
            job["logs"].append("Pipeline running...")
            job["version"] += 1

        def observe(event: dict[str, Any]) -> None:
            with pipeline_jobs_lock:
                job = pipeline_jobs[job_id]
                stage = job["stages"][event["stage"]]
                if event.get("started"):
                    stage.clear()
                    title = {"actor": "Actor Stage", "arbiter": "Arbiter Stage", "gatekeeper": "Gatekeeper Stage", "bbox": "Bounding Box Generation"}[event["stage"]]
                    job["logs"].append(f"{title} | Cycle {event['cycle']} Running")
                elif event["status"] == "completed":
                    title = {"actor": "Actor Stage", "arbiter": "Arbiter Stage", "gatekeeper": "Gatekeeper Stage", "bbox": "Bounding Box Generation"}[event["stage"]]
                    job["logs"].append(f"{title} | Cycle {event['cycle']} Completed")
                stage.update(status=event["status"], cycle=event["cycle"], step=copy.deepcopy(event["step"]),
                             error=event.get("error"))
                progress = event.get("bbox_progress", {})
                if progress.get("stage") == "intents":
                    stage["intents"] = copy.deepcopy(progress["intents"])
                elif progress.get("stage") == "edge":
                    stage.setdefault("edges", {})[progress["axis"]] = progress["coordinate"]
                job["version"] += 1

        try:
            result = EndToEndPipeline().run_step(episode_id, step_index, output_id=output_id, progress_callback=observe)
            status, reason, error = result.status, result.reason, result.error
        except Exception as exc:
            status, reason, error = "failed", "pipeline_error", f"{type(exc).__name__}: {exc}"
        with pipeline_jobs_lock:
            job = pipeline_jobs[job_id]
            job.update(status=status, reason=reason, error=error)
            reasons = {
                "actors_reproduced": "Both Actors reproduced the reference action.",
                "arbiters_reproduced": "Both Arbiters reproduced the reference action.",
                "case_a": "Case A: this step is excluded.",
                "case_b": "Case B: the reference action is retained.",
                "gatekeeper_limit": "Case C at the third Gatekeeper decision: this step is excluded.",
            }
            job["logs"].append(f"Pipeline failed: {error}" if status == "failed" else reasons.get(reason, "Pipeline completed."))
            job["logs"].append("Pipeline finished.")
            job["version"] += 1

    def run_actor_job(
        *,
        actor_role: str,
        episode_id: str,
        output_id: str,
        step_index: int,
    ):
        provider = provider_for_role(actor_role)
        return Actor(provider).run(
            episode_id,
            step_index,
            output_field=f"{actor_role}_output",
            output_path=output_data_path(repository_root(), episode_id, output_id),
        )

    def run_arbiter_job(
        *,
        arbiter_role: str,
        episode_id: str,
        output_id: str,
        step_index: int,
        decision_seed: str,
    ):
        provider = provider_for_role(arbiter_role)
        return Arbiter(provider).run(
            episode_id,
            step_index,
            output_field=f"{arbiter_role}_output",
            output_path=output_data_path(repository_root(), episode_id, output_id),
            decision_seed=decision_seed,
        )

    def run_gatekeeper_job(
        *,
        episode_id: str,
        output_id: str,
        step_index: int,
        decision_seed: str,
    ):
        return Gatekeeper(provider_for_role("gatekeeper")).run(
            episode_id,
            step_index,
            output_path=output_data_path(repository_root(), episode_id, output_id),
            decision_seed=decision_seed,
        )

    def run_bbox_job(*, job_id: str, episode_id: str, output_id: str, step_index: int):
        def report_progress(payload: dict[str, Any]) -> None:
            with bbox_jobs_lock:
                job = bbox_jobs.get(job_id)
                if job is None:
                    return
                if payload.get("stage") == "intents":
                    job["intents"] = dict(payload.get("intents") or {})
                elif payload.get("stage") == "edge":
                    axis, coordinate = payload.get("axis"), payload.get("coordinate")
                    if axis in {"top", "bottom", "left", "right"} and isinstance(coordinate, int):
                        job["edges"][axis] = coordinate

        return BBoxGenerator().generate_for_output(
            episode_id,
            output_id,
            step_index,
            repository_root=repository_root(),
            progress_callback=report_progress,
        )

    @app.get("/assets/<path:filename>")
    def web_asset(filename: str):
        """Serve the small set of layout assets bundled with the web package."""

        return send_from_directory(app.root_path + "/assets", filename)

    @app.get("/")
    def pipeline_page():
        episodes = list_episode_ids()
        view = pipeline_selection(episodes[0]) if episodes else None
        return render_template("pipeline_test.html", tabs=_TABS, active_tab="pipeline", initial_view=view)

    @app.get("/api/pipeline/selection/<episode_id>/<int:step_index>")
    def pipeline_selection_api(episode_id: str, step_index: int):
        try:
            return jsonify(pipeline_selection(episode_id, step_index))
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/pipeline/run")
    def pipeline_run_api():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "output_id" in payload:
            return jsonify({"error": "Select an episode and step; every run creates a new output."}), 400
        episode_id = str(payload.get("episode_id") or "").strip()
        step_index = payload.get("step_index")
        try:
            sample = pipeline_sample(episode_id, step_index)
            providers = {role: provider_for_role(role) for role in ("actor1", "actor2", "arbiter1", "arbiter2", "gatekeeper")}
            output_id, _ = new_pipeline_output(episode_id, step_index)
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400
        job_id = uuid.uuid4().hex
        with pipeline_jobs_lock:
            pipeline_jobs[job_id] = {
                "episode_id": episode_id, "step_index": step_index, "output_id": output_id,
                "status": "queued", "reason": None, "error": None, "version": 0,
                "logs": ["Pipeline starting..."],
                "providers": providers, "stages": initial_stages(sample["steps"][step_index]),
            }
        pipeline_executor.submit(run_pipeline_job, job_id)
        return jsonify({"job_id": job_id, "output_id": output_id, "status": "queued"}), 202

    @app.get("/api/pipeline/run/<job_id>")
    def pipeline_run_status_api(job_id: str):
        with pipeline_jobs_lock:
            if job_id not in pipeline_jobs:
                return jsonify({"error": "Unknown pipeline run"}), 404
            job = copy.deepcopy(pipeline_jobs[job_id])
        if request.args.get("after") == str(job["version"]):
            return jsonify({key: value for key, value in job.items() if key != "stages"})
        job["stages"] = pipeline_stage_views(job["episode_id"], job["stages"])
        return jsonify(job)

    @app.get("/actor")
    def actor_page():
        episodes = list_episode_ids()
        initial_view = actor_view(episodes[0], 0) if episodes else None
        return render_template("actor_test.html", tabs=_TABS, active_tab="actor", initial_view=initial_view)

    @app.get("/api/actor/episode/<episode_id>")
    def actor_episode_api(episode_id: str):
        try:
            return jsonify(actor_view(episode_id, 0))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/actor/view/<episode_id>/<output_id>/<int:step_index>")
    def actor_view_api(episode_id: str, output_id: str, step_index: int):
        try:
            return jsonify(actor_view(episode_id, step_index, output_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/actor/run")
    def actor_run_api():
        payload = request.get_json(silent=True) or {}
        actor_role = str(payload.get("actor_role") or "").strip()
        episode_id = str(payload.get("episode_id") or "").strip()
        output_id = str(payload.get("output_id") or "").strip()
        step_index = payload.get("step_index")
        if actor_role not in {"actor1", "actor2"}:
            return jsonify({"error": "actor_role must be actor1 or actor2"}), 400
        if not isinstance(step_index, int) or isinstance(step_index, bool):
            return jsonify({"error": "step_index must be an integer"}), 400
        try:
            actor_view(episode_id, step_index, output_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

        job_id = uuid.uuid4().hex
        future = actor_executor.submit(
            run_actor_job,
            actor_role=actor_role,
            episode_id=episode_id,
            output_id=output_id,
            step_index=step_index,
        )
        with actor_jobs_lock:
            actor_jobs[job_id] = {
                "future": future,
                "actor_role": actor_role,
                "episode_id": episode_id,
                "output_id": output_id,
                "step_index": step_index,
            }
        return jsonify({"job_id": job_id, "status": "running"}), 202

    @app.get("/api/actor/run/<job_id>")
    def actor_run_status_api(job_id: str):
        with actor_jobs_lock:
            job = actor_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Unknown Actor job"}), 404
        future = job["future"]
        if not future.done():
            return jsonify({"status": "running"})
        try:
            result = future.result()
            view = actor_view(job["episode_id"], job["step_index"], job["output_id"])
        except Exception as exc:
            return jsonify({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}), 500
        return jsonify(
            {
                "status": "completed",
                "actor_role": job["actor_role"],
                "error": result.error,
                "view": view,
            }
        )

    @app.get("/arbiter")
    def arbiter_page():
        episodes = list_arbiter_episode_ids()
        initial_view = arbiter_view(episodes[0]) if episodes else None
        return render_template("arbiter_test.html", tabs=_TABS, active_tab="arbiter", initial_view=initial_view)

    @app.get("/api/arbiter/episode/<episode_id>")
    def arbiter_episode_api(episode_id: str):
        try:
            return jsonify(arbiter_view(episode_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/arbiter/view/<episode_id>/<output_id>/<int:step_index>")
    def arbiter_view_api(episode_id: str, output_id: str, step_index: int):
        try:
            return jsonify(arbiter_view(episode_id, output_id, step_index))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/arbiter/run")
    def arbiter_run_api():
        payload = request.get_json(silent=True) or {}
        arbiter_role = str(payload.get("arbiter_role") or "").strip()
        episode_id = str(payload.get("episode_id") or "").strip()
        output_id = str(payload.get("output_id") or "").strip()
        step_index = payload.get("step_index")
        decision_seed = str(payload.get("decision_seed") or "").strip()
        if arbiter_role not in {"arbiter1", "arbiter2"}:
            return jsonify({"error": "arbiter_role must be arbiter1 or arbiter2"}), 400
        if not isinstance(step_index, int) or isinstance(step_index, bool):
            return jsonify({"error": "step_index must be an integer"}), 400
        try:
            view = arbiter_view(episode_id, output_id, step_index)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        expected_seed = view["decision_seed"]
        if decision_seed != expected_seed:
            return jsonify({"error": "The displayed Model Decisions are no longer current. Reload the step and try again."}), 409

        job_id = uuid.uuid4().hex
        future = arbiter_executor.submit(
            run_arbiter_job,
            arbiter_role=arbiter_role,
            episode_id=episode_id,
            output_id=output_id,
            step_index=step_index,
            decision_seed=decision_seed,
        )
        with arbiter_jobs_lock:
            arbiter_jobs[job_id] = {
                "future": future,
                "arbiter_role": arbiter_role,
                "episode_id": episode_id,
                "output_id": output_id,
                "step_index": step_index,
            }
        return jsonify({"job_id": job_id, "status": "running"}), 202

    @app.get("/api/arbiter/run/<job_id>")
    def arbiter_run_status_api(job_id: str):
        with arbiter_jobs_lock:
            job = arbiter_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Unknown Arbiter job"}), 404
        future = job["future"]
        if not future.done():
            return jsonify({"status": "running"})
        try:
            result = future.result()
            view = arbiter_view(job["episode_id"], job["output_id"], job["step_index"])
        except Exception as exc:
            return jsonify({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}), 500
        return jsonify({"status": "completed", "arbiter_role": job["arbiter_role"], "error": result.error, "view": view})

    @app.get("/gatekeeper")
    def gatekeeper_page():
        episodes = list_gatekeeper_episode_ids()
        initial_view = gatekeeper_view(episodes[0]) if episodes else None
        return render_template("gatekeeper_test.html", tabs=_TABS, active_tab="gatekeeper", initial_view=initial_view)

    @app.get("/api/gatekeeper/episode/<episode_id>")
    def gatekeeper_episode_api(episode_id: str):
        try:
            return jsonify(gatekeeper_view(episode_id))
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/gatekeeper/view/<episode_id>/<output_id>")
    @app.get("/api/gatekeeper/view/<episode_id>/<output_id>/<int:step_index>")
    def gatekeeper_view_api(episode_id: str, output_id: str, step_index: int | None = None):
        try:
            return jsonify(gatekeeper_view(episode_id, output_id, step_index))
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/gatekeeper/run")
    def gatekeeper_run_api():
        payload = request.get_json(silent=True) or {}
        episode_id = str(payload.get("episode_id") or "").strip()
        output_id = str(payload.get("output_id") or "").strip()
        step_index = payload.get("step_index")
        decision_seed = str(payload.get("decision_seed") or "").strip()
        if not isinstance(step_index, int) or isinstance(step_index, bool):
            return jsonify({"error": "step_index must be an integer"}), 400
        try:
            view = gatekeeper_view(episode_id, output_id, step_index)
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 404
        if decision_seed != view["decision_seed"]:
            return jsonify({"error": "The displayed Arbiter-selected actions are no longer current. Reload the step and try again."}), 409

        job_id = uuid.uuid4().hex
        future = gatekeeper_executor.submit(
            run_gatekeeper_job,
            episode_id=episode_id,
            output_id=output_id,
            step_index=step_index,
            decision_seed=decision_seed,
        )
        with gatekeeper_jobs_lock:
            gatekeeper_jobs[job_id] = {
                "future": future,
                "episode_id": episode_id,
                "output_id": output_id,
                "step_index": step_index,
            }
        return jsonify({"job_id": job_id, "status": "running"}), 202

    @app.get("/api/gatekeeper/run/<job_id>")
    def gatekeeper_run_status_api(job_id: str):
        with gatekeeper_jobs_lock:
            job = gatekeeper_jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Unknown Gatekeeper job"}), 404
        future = job["future"]
        if not future.done():
            return jsonify({"status": "running"})
        try:
            result = future.result()
            view = gatekeeper_view(job["episode_id"], job["output_id"], job["step_index"])
        except Exception as exc:
            return jsonify({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}), 500
        return jsonify({"status": "completed", "error": result.error, "view": view})

    @app.get("/bbox-generation")
    def bbox_generation_page():
        episodes = list_bbox_generation_episode_ids()
        initial_view = bbox_generation_view(episodes[0]) if episodes else None
        return render_template("bbox_generation_test.html", tabs=_TABS, active_tab="bbox-generation", initial_view=initial_view)

    @app.get("/api/bbox-generation/episode/<episode_id>")
    def bbox_generation_episode_api(episode_id: str):
        try:
            return jsonify(bbox_generation_view(episode_id))
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/bbox-generation/view/<episode_id>/<output_id>/<int:step_index>")
    def bbox_generation_view_api(episode_id: str, output_id: str, step_index: int):
        try:
            return jsonify(bbox_generation_view(episode_id, output_id, step_index))
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/bbox-generation/run")
    def bbox_generation_run_api():
        payload = request.get_json(silent=True) or {}
        episode_id = str(payload.get("episode_id") or "").strip()
        output_id = str(payload.get("output_id") or "").strip()
        step_index = payload.get("step_index")
        if not isinstance(step_index, int) or isinstance(step_index, bool):
            return jsonify({"error": "step_index must be an integer"}), 400
        try:
            bbox_generation_view(episode_id, output_id, step_index)
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 404

        job_id = uuid.uuid4().hex
        with bbox_jobs_lock:
            bbox_jobs[job_id] = {
                "future": None,
                "episode_id": episode_id,
                "output_id": output_id,
                "step_index": step_index,
                "intents": {},
                "edges": {},
            }
        future = bbox_executor.submit(
            run_bbox_job,
            job_id=job_id,
            episode_id=episode_id,
            output_id=output_id,
            step_index=step_index,
        )
        with bbox_jobs_lock:
            bbox_jobs[job_id]["future"] = future
        return jsonify({"job_id": job_id, "status": "running"}), 202

    @app.get("/api/bbox-generation/run/<job_id>")
    def bbox_generation_run_status_api(job_id: str):
        with bbox_jobs_lock:
            job = bbox_jobs.get(job_id)
            if job is None:
                return jsonify({"error": "Unknown bounding-box generation job"}), 404
            future = job["future"]
            progress = {"intents": dict(job["intents"]), "edges": dict(job["edges"])}
        if future is None or not future.done():
            return jsonify({"status": "running", **progress})
        try:
            result = future.result()
            view = bbox_generation_view(job["episode_id"], job["output_id"], job["step_index"])
        except Exception as exc:
            return jsonify({"status": "failed", "error": f"{type(exc).__name__}: {exc}", **progress}), 500
        return jsonify({
            "status": "completed",
            "bbox": result.bbox,
            "source": result.source,
            "view": view,
            **progress,
        })

    return app


def main() -> int:
    """Validate local configuration, then run Flask in the foreground."""

    root = repository_root()
    errors = validate_startup(root)
    if errors:
        print(startup_error_message(errors))
        return 1
    dotenv_errors = load_dotenv(root / ".env")
    if dotenv_errors:
        print(startup_error_message(dotenv_errors))
        return 1

    host = os.getenv("WEB_HOST", "0.0.0.0")
    port_text = os.getenv("WEB_PORT", "8000")
    try:
        port = int(port_text)
    except ValueError:
        print("The web application was not started. WEB_PORT must be an integer.")
        return 1
    if not 1 <= port <= 65535:
        print("The web application was not started. WEB_PORT must be between 1 and 65535.")
        return 1

    create_app().run(host=host, port=port, debug=False, load_dotenv=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
