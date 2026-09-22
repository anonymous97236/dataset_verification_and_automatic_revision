(() => {
  const initial = JSON.parse(document.getElementById('pipeline-initial-data').textContent);
  if (!initial) return;
  const episode = document.getElementById('episode-select');
  const step = document.getElementById('step-select');
  const run = document.getElementById('run-pipeline-button');
  const message = document.getElementById('pipeline-message');
  const errorBox = document.getElementById('pipeline-error');
  const stageNames = {actor: 'Actor Stage', arbiter: 'Arbiter Stage', gatekeeper: 'Gatekeeper Stage', bbox: 'Bounding Box Generation'};
  let selection = initial, active = null, loadSequence = 0;

  function appendLog(text) {
    message.value += `${message.value ? '\n' : ''}${text}`;
    message.scrollTop = message.scrollHeight;
  }
  function appendJobLogs(job, logs = []) {
    if (job.logCount === undefined) {
      appendLog(`\nEpisode: ${job.episode_id} | Step ${job.step_index + 1} | Output ID: ${job.output_id}`);
      job.logCount = 0;
    }
    logs.slice(job.logCount).forEach(appendLog);
    job.logCount = logs.length;
  }

  const valueText = value => value === null || value === undefined || value === '' ? '—' : typeof value === 'string' ? value : JSON.stringify(value);
  function fields(id, entries, empty = 'Waiting for this stage.') {
    const container = document.getElementById(id);
    if (!entries.length) {
      const p = document.createElement('p'); p.className = 'action-empty'; p.textContent = empty;
      container.replaceChildren(p); return;
    }
    container.replaceChildren(...entries.map(({name, value}) => {
      const field = document.createElement('div'); field.className = 'action-field';
      const heading = document.createElement('span'); heading.textContent = name;
      const p = document.createElement('p'); p.textContent = valueText(value);
      field.append(heading, p); return field;
    }));
  }
  function image(id, source) {
    const element = document.getElementById(id);
    if (source && element.getAttribute('src') !== source) element.src = source;
  }
  function drawBox(view) {
    const edges = {...view.edges};
    if (view.bbox) [edges.left, edges.top, edges.right, edges.bottom] = view.bbox;
    const coords = view.bbox || [edges.left, edges.top, edges.right, edges.bottom];
    document.getElementById('bbox-coordinates').textContent = `[${coords.map(value => value ?? '-').join(', ')}]`;
    const svg = document.getElementById('bbox-overlay'); svg.replaceChildren();
    function shape(tag, attributes) {
      const item = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attributes).forEach(([key, value]) => item.setAttribute(key, value)); svg.append(item);
    }
    ['left', 'right'].forEach(axis => {
      if (edges[axis] !== undefined) shape('line', {x1: edges[axis], x2: edges[axis], y1: 0, y2: 1000, class: 'bbox-guide-line'});
    });
    ['top', 'bottom'].forEach(axis => {
      if (edges[axis] !== undefined) shape('line', {x1: 0, x2: 1000, y1: edges[axis], y2: edges[axis], class: 'bbox-guide-line'});
    });
    if (view.bbox) {
      const [x1, y1, x2, y2] = view.bbox;
      shape('rect', {x: x1, y: y1, width: x2 - x1, height: y2 - y1, class: 'bbox-final-rectangle'});
    }
    const names = view.source === 'od_results' ? ['Target'] : ['Target', 'Top', 'Bottom', 'Left', 'Right'];
    document.getElementById('bbox-intents').replaceChildren(...names.map(name => {
      const field = document.createElement('div'); field.className = 'bbox-intent-field';
      const label = document.createElement('span'); label.textContent = name;
      const p = document.createElement('p'); p.textContent = valueText((view.intents || {})[name]);
      field.append(label, p); return field;
    }));
  }
  function renderStages(stages, providers) {
    Object.entries(providers).forEach(([role, provider]) => {
      document.getElementById(`${role}-provider`).textContent = `Provider: ${provider === 'gemini' ? 'Gemini' : 'Qwen'}`;
    });
    Object.entries(stages).forEach(([name, view]) => {
      const running = view.status === 'running';
      const suffix = view.cycle ? ` | Cycle ${view.cycle} ${running ? 'Running' : view.status === 'failed' ? 'Failed' : 'Completed'}` : '';
      const title = document.getElementById(`${name}-stage-title`);
      title.textContent = `${running ? '🟢' : '⚪'} ${stageNames[name]}${suffix}`;
      if (name === 'actor' || name === 'arbiter') {
        fields(`${name}-reference`, view.reference_action);
        image(`${name}-screenshot`, view.screenshot);
        Object.entries(view.outputs).forEach(([role, output]) => {
          document.getElementById(`${role}-thought`).textContent = output.error || output.thought ||
            (view.error || (running && !output.available ? (name === 'actor' ? 'Inferring action...' : 'Selecting an action...') : 'Waiting for this stage.'));
          fields(`${role}-fields`, output.fields);
          const badge = document.getElementById(`${role}-reproduction`);
          badge.hidden = !output.reproduction; badge.className = 'reproduction-status';
          if (output.reproduction) { badge.classList.add(output.reproduction.result); badge.textContent = output.reproduction.label; }
        });
      } else if (name === 'gatekeeper') {
        document.getElementById('gatekeeper-thought').textContent = view.error || view.thought || (running ? 'Classifying the case...' : 'Waiting for this stage.');
        document.querySelectorAll('.gatekeeper-case-item').forEach(item => item.classList.toggle('gatekeeper-case-selected', item.dataset.case === view.case));
      } else {
        fields('bbox-reference', view.reference_action); image('bbox-screenshot', view.screenshot); drawBox(view);
        document.getElementById('bbox-message').textContent = view.error || (running ? 'Generating the bounding box...' : view.status === 'completed' ? 'Bounding box generation completed.' : 'Waiting for a revised coordinate action.');
      }
    });
  }
  function options(select, items, selected) {
    select.replaceChildren(...items.map(({value, label}) => {
      const option = document.createElement('option'); option.value = value; option.textContent = label;
      option.selected = String(value) === String(selected); return option;
    }));
  }
  function renderSelection(view) {
    selection = view;
    options(episode, view.episodes.map(value => ({value, label: value})), view.episode_id);
    options(step, view.steps.map(item => ({value: item.index, label: item.label})), view.step_index);
    renderStages(view.stages, view.providers);
    document.getElementById('pipeline-output').textContent = '';
  }
  async function fetchJSON(url, options) {
    const response = await fetch(url, options); const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || 'Unable to load pipeline data.'); error.status = response.status; throw error;
    }
    return result;
  }
  async function loadSelection(index) {
    const sequence = ++loadSequence;
    run.disabled = true; errorBox.hidden = true;
    try {
      const view = await fetchJSON(`/api/pipeline/selection/${encodeURIComponent(episode.value)}/${index}`);
      if (sequence === loadSequence) {
        renderSelection(view);
        if (active) active.version = -1;
      }
    } catch (error) {
      if (sequence === loadSequence) { renderSelection(selection); errorBox.textContent = error.message; errorBox.hidden = false; }
    } finally { if (sequence === loadSequence) run.disabled = Boolean(active); }
  }
  function matches(job) { return job.episode_id === episode.value && String(job.step_index) === step.value; }
  async function poll() {
    if (!active) return;
    const job = active;
    try {
      const result = await fetchJSON(`/api/pipeline/run/${job.job_id}?after=${job.version}`);
      if (active !== job) return;
      appendJobLogs(job, result.logs);
      if (matches(job)) {
        if (result.stages) renderStages(result.stages, result.providers);
        document.getElementById('pipeline-output').textContent = `Output ID: ${job.output_id}`;
      }
      job.version = result.version;
      if (['completed', 'excluded', 'failed'].includes(result.status)) {
        active = null; sessionStorage.removeItem('pipeline-job'); run.disabled = false;
      } else { window.setTimeout(poll, 600); }
    } catch (error) {
      errorBox.textContent = `Unable to observe the run: ${error.message} Reload this page to check its status.`; errorBox.hidden = false;
      if (error.status === 404) { active = null; sessionStorage.removeItem('pipeline-job'); run.disabled = false; }
      // Do not resubmit the pipeline or any model request after an observation error.
    }
  }
  async function start() {
    run.disabled = true; errorBox.hidden = true;
    try {
      const target = {episode_id: episode.value, step_index: Number(step.value)};
      const job = await fetchJSON('/api/pipeline/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(target)});
      active = {...target, ...job, version: -1};
      sessionStorage.setItem('pipeline-job', JSON.stringify(active));
      renderStages(selection.stages, selection.providers);
      document.getElementById('pipeline-output').textContent = `Output ID: ${job.output_id}`;
      poll();
    } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; run.disabled = false; }
  }
  renderSelection(initial);
  episode.addEventListener('change', () => loadSelection(0));
  step.addEventListener('change', () => loadSelection(Number(step.value)));
  run.addEventListener('click', start);
  // Reloading/navigating back only resumes observation, never execution.
  const saved = sessionStorage.getItem('pipeline-job');
  if (saved) {
    try {
      active = {...JSON.parse(saved), version: -1}; run.disabled = true;
      fetchJSON(`/api/pipeline/selection/${encodeURIComponent(active.episode_id)}/${active.step_index}`)
        .then(view => { renderSelection(view); poll(); })
        .catch(error => { active = null; sessionStorage.removeItem('pipeline-job'); run.disabled = false; errorBox.textContent = error.message; errorBox.hidden = false; });
    } catch (_) { sessionStorage.removeItem('pipeline-job'); active = null; run.disabled = false; }
  }
})();
