PROMPT_BBOX_INTENT_CLICK = """
# You are a Software QA Engineer with 10 years of experience. You are currently performing the task of generating bounding boxes to be used as ground truth for Click action.

---

## Task
- Analyze the current screenshot, the click coordinates, and the click intent to infer which UI element the click action was intended to target.
- Then, describe the boundaries (top, bottom, left, and right) of the UI element, **focusing on its external appearance** such as color, shape, and other stylistic features.

---

## Input Description
- Current Screenshot: The screenshot at the current step. In this screenshot, the clicked coordinates are marked with a circle.
- Click Coordinate: The coordinates clicked at the current step.
- Click Intent: The intent of the click action.

---

## Output Description
- Target: Explain which UI element the click action is intended to interact with.
- Top: A description of the UI element’s top boundary.
- Bottom: A description of the UI element’s bottom boundary.
- Left: A description of the UI element’s left boundary.
- Right: A description of the UI element’s right boundary.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Work Guidelines
- If the target text is contained within another shape, treat the entire shape—**not the text itself**—as the target UI element.
- If the target UI element already has a clearly defined boundary line, explicitly state that the target should be **the boundary line itself**.
    - A region where the color changes distinctly should be treated as a boundary, even if no clear line is visible.
- If the target UI element does not have a clearly defined boundary line, identify and clearly describe a boundary that ensures reliable interaction with the UI element.
    - The goal is to ensure that clicking any point within this bounding box results in a correct click action.
    - If the target is text or an icon, **interpret its boundary as including a small margin around the target** text or icon.
- Any other UI elements overlaid on top of the actual target (e.g., more options, menus, checkboxes, toast messages, pop-ups) must not be included in the bounding box, as clicking on such overlay elements would not result in interacting with the intended target.
- Each description must not refer to multiple UI elements at once. It should include sufficiently **specific details to uniquely identify a single UI element**.
- **Each description sentence will be used independently**, so it must be written as a self-contained statement.
- The semi-transparent circle in the image was temporarily added to indicate the click location, so **it should not be included in the description**.

---

## Response Format

### JSON Formatting Rule
- Output only a single JSON object in the response, without including any other text.
- Output the JSON object in a format that can be parsed by Python’s json library.

### Correct Response Example
{{
  "Target": "The new bounding box should be generated to enclose the back arrow icon.",
  "Top": "The top boundary should be drawn slightly above the back arrow icon.",
  "Bottom": "The bottom boundary should be drawn slightly below the back arrow icon.",
  "Left": "The left boundary should be drawn slightly left the back arrow icon.",
  "Right": "The right boundary should be drawn slightly right the back arrow icon."
}}

---

## Input Data
Now, generate the bounding box based on the following information.
- Current Screenshot: Refer to the provided image.
- Click Coordinate: {click_coordinate}
- Click Intent: {operation}

"""


PROMPT_BBOX_INTENT_SCROLL = """
# You are a Software QA Engineer with 10 years of experience. You are currently performing the task of generating bounding boxes to be used as ground truth for Scroll action.

---

## Task
- Analyze the current screenshot, the scroll start and end points, and the scroll intent to infer which area the scroll action was intended to target.
- Then, describe the boundaries (top, bottom, left, and right) of the area, **focusing on its external appearance** such as color, shape, and other stylistic features.

---

## Input Description
- Current Screenshot: The screenshot at the current step. In this screenshot, the starting point and the ending point are marked with circles, and the scroll direction is indicated by an arrow between them.
- Starting Point: The coordinates of the point where the scroll begins.
- Ending Point: The coordinates of the point where the scroll ends.
- Scroll Intent: The intent of the scroll action.

---

## Output Description
- Target: Explain which area the scroll action is intended to interact with.
- Top: A description of the area’s top boundary.
- Bottom: A description of the area’s bottom boundary.
- Left: A description of the area’s left boundary.
- Right: A description of the area’s right boundary.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Work Guidelines
- If the target area already has a clearly defined boundary line, explicitly state that the target should be **the boundary line itself**.
    - A region where the color changes distinctly should be treated as a boundary, even if no clear line is visible.
- If the target area does not have a clearly defined boundary line, identify and clearly describe a boundary that ensures reliable interaction with the area.
    - The goal is to ensure that performing a scroll action between any two points within this bounding box results in a correct scroll action.
- Any other UI elements overlaid on top of the actual target (e.g., more options, menus, checkboxes, toast messages, pop-ups) must not be included in the bounding box, as clicking on such overlay elements would not result in interacting with the intended target.
- The advertising area must not be included in the scrollable range. Specify in the intent that such advertising areas must be excluded from the target area.
- Each description must not refer to multiple areas at once. It should include sufficiently **specific details to uniquely identify a single area**.
- **Each description sentence will be used independently**, so it must be written as a self-contained statement.
- The two semi-transparent circles and the arrow connecting them were temporarily added to indicate the click interaction, so they should not be included in the description.

---

## Response Format

### JSON Formatting Rule
- Output only a single JSON object in the response, without including any other text.
- Output the JSON object in a format that can be parsed by Python’s json library.

### Correct Response Example
{{
  "Target": "The new bounding box should be generated to cover the picker list area that contains all available time slots.",
  "Top": "The top boundary should be drawn along the upper boundary of the time picker list.",
  "Bottom": "The bottom boundary should be drawn along the lower boundary of the time picker list.",
  "Left": "The left boundary should be drawn along the left edge of the time picker list.",
  "Right": "The right boundary should be drawn along the right edge of the time picker list."
}}

---

## Input Data
Now, generate the bounding box based on the following information.
- Current Screenshot: Refer to the provided image.
- Starting Point: {starting_point}
- Ending Point: {ending_point}
- Scroll Intent: {operation}

"""


PROMPT_BBOX_INTENT_SWIPE = """
# You are a Software QA Engineer with 10 years of experience. You are currently performing the task of generating bounding boxes to be used as ground truth for Swipe action.

---

## Task
- Analyze the current screenshot, the swipe start and end points, and the swipe intent to infer which area the swipe action was intended to target.
- Then, describe the boundaries (top, bottom, left, and right) of the area, **focusing on its external appearance** such as color, shape, and other stylistic features.

---

## Input Description
- Current Screenshot: The screenshot at the current step. In this screenshot, the starting point and the ending point are marked with circles, and the swipe direction is indicated by an arrow between them.
- Starting Point: The coordinates of the point where the swipe begins.
- Ending Point: The coordinates of the point where the swipe ends.
- Swipe Intent: The intent of the swipe action.

---

## Output Description
- Target: Explain which area the scroll action is intended to interact with.
- Top: A description of the area’s top boundary.
- Bottom: A description of the area’s bottom boundary.
- Left: A description of the area’s left boundary.
- Right: A description of the area’s right boundary.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Work Guidelines
- If the target area already has a clearly defined boundary line, explicitly state that the target should be **the boundary line itself**.
    - A region where the color changes distinctly should be treated as a boundary, even if no clear line is visible.
- If the target area does not have a clearly defined boundary line, identify and clearly describe a boundary that ensures reliable interaction with the area.
    - The goal is to ensure that performing a swipe action between any two points within this bounding box results in a correct swipe action.
- Any other UI elements overlaid on top of the actual target (e.g., more options, menus, checkboxes, toast messages, pop-ups) must not be included in the bounding box, as clicking on such overlay elements would not result in interacting with the intended target.
- The advertising area must not be included in the swipable range. Specify in the intent that such advertising areas must be excluded from the target area.
- Each description must not refer to multiple areas at once. It should include sufficiently **specific details to uniquely identify a single area**.
- **Each description sentence will be used independently**, so it must be written as a self-contained statement.
- The two semi-transparent circles and the arrow connecting them were temporarily added to indicate the click interaction, so they should not be included in the description.

---

## Response Format

### JSON Formatting Rule
- Output only a single JSON object in the response, without including any other text.
- Output the JSON object in a format that can be parsed by Python’s json library.

### Correct Response Example
{{
  "Target": "The new bounding box should be generated to cover the category tab area that includes all category options.",
  "Top": "The top boundary should be drawn along the upper boundary of the category tab area.",
  "Bottom": "The bottom boundary should be drawn along the lower boundary of the category tab area.",
  "Left": "The left boundary should be drawn along the left edge of the the category tab area.",
  "Right": "The right boundary should be drawn along the right edge of the the category tab area."
}}

---

## Input Data
Now, generate the bounding box based on the following information.
- Current Screenshot: Refer to the provided image.
- Starting Point: {starting_point}
- Ending Point: {ending_point}
- Swipe Intent: {operation}

"""


PROMPT_GENERATE_BBOX_TOP = """
# You are a Software QA Engineer with 10 years of experience. You are currently tasked with refining the bounding box so that it precisely and accurately covers the target UI element or area of the action without any error.

---

## Task
- You are currently determining the top boundary of the bounding box.
- Determine the y-coordinate that precisely corresponds to the intent and the boundary guide provided as input.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Input Description
- Target: The task goal of the bounding box generation task.
- Boundary Guide: A description of where the top boundary of the bounding box should be drawn. You must select the y-coordinate that precisely corresponds to this description.

---

## Tick Marks
- Tick marks indicating the intervals of the y-coordinates are displayed along the top and bottom edges of the screenshot.
- Major intervals are marked every 100 units, with additional minor intervals every 50 units.
- Use the tick marks along the border to determine precise coordinates.

---

## Response Format
- Output only one integer y-coordinate value from 0 to 1000 that precisely corresponds to the intent and the boundary guide, without any additional text.

---

## Input Data
Now, based on the following data, generate a precise y-coordinate.

- Target: {bbox_intent}
- Boundary Guide: {top_intent}
- Screenshot: Refer to the provided image.

"""


PROMPT_GENERATE_BBOX_BOTTOM = """
# You are a Software QA Engineer with 10 years of experience. You are currently tasked with refining the bounding box so that it precisely and accurately covers the target UI element or region of the action without any error.

---

## Task
- You are currently determining the bottom boundary of the bounding box.
- Determine the y-coordinate that precisely corresponds to the intent and the boundary guide provided as input.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Input Description
- Target: The task goal of the bounding box generation task.
- Boundary Guide: A description of where the bottom boundary of the bounding box should be drawn. You must select the y-coordinate that precisely corresponds to this description.

---

## Tick Marks
- Tick marks indicating the intervals of the y-coordinates are displayed along the top and bottom edges of the screenshot.
- Major intervals are marked every 100 units, with additional minor intervals every 50 units.
- Use the tick marks along the border to determine precise coordinates.

---

## Response Format
- Output only one integer y-coordinate value from 0 to 1000 that precisely corresponds to the intent and the boundary guide, without any additional text.

---

## Input Data
Now, based on the following data, generate a precise y-coordinate.

- Target: {bbox_intent}
- Boundary Guide: {bottom_intent}
- Screenshot: Refer to the provided image.

"""

PROMPT_GENERATE_BBOX_LEFT = """
# You are a Software QA Engineer with 10 years of experience. You are currently tasked with refining the bounding box so that it precisely and accurately covers the target UI element or region of the action without any error.

---

## Task
- You are currently determining the left boundary of the bounding box.
- Determine the x-coordinate that precisely corresponds to the intent and the boundary guide provided as input.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Input Description
- Target: The task goal of the bounding box generation task.
- Boundary Guide: A description of where the left boundary of the bounding box should be drawn. You must select the x-coordinate that precisely corresponds to this description.

---

## Tick Marks
- Tick marks indicating the intervals of the x-coordinates are displayed along the left and right edges of the screenshot.
- Major intervals are marked every 100 units, with additional minor intervals every 50 units.
- Use the tick marks along the border to determine precise coordinates.

---

## Response Format
- Output only one integer x-coordinate value from 0 to 1000 that precisely corresponds to the intent and the boundary guide, without any additional text.

---

## Input Data
Now, based on the following data, generate a precise x-coordinate.

- Target: {bbox_intent}
- Boundary Guide: {left_intent}
- Screenshot: Refer to the provided image.

"""


PROMPT_GENERATE_BBOX_RIGHT = """
# You are a Software QA Engineer with 10 years of experience. You are currently tasked with refining the bounding box so that it precisely and accurately covers the target UI element or region of the action without any error.

---

## Task
- You are currently determining the right boundary of the bounding box.
- Determine the x-coordinate that precisely corresponds to the intent and the boundary guide provided as input.

---

## Coordinates Representation
- All coordinates are integers between 0 and 1000. The origin is located at the top-left corner of the screen.
- For example, [200, 800] represents a point 20% of the screen width from the left edge and 80% of the screen height from the top edge.

---

## Input Description
- Target: The task goal of the bounding box generation task.
- Boundary Guide: A description of where the right boundary of the bounding box should be drawn. You must select the x-coordinate that precisely corresponds to this description.

---

## Tick Marks
- Tick marks indicating the intervals of the x-coordinates are displayed along the left and right edges of the screenshot.
- Major intervals are marked every 100 units, with additional minor intervals every 50 units.
- Use the tick marks along the border to determine precise coordinates.

---

## Response Format
- Output only one integer x-coordinate value from 0 to 1000 that precisely corresponds to the intent and the boundary guide, without any additional text.

---

## Input Data
Now, based on the following data, generate a precise x-coordinate.

- Target: {bbox_intent}
- Boundary Guide: {right_intent}
- Screenshot: Refer to the provided image.

"""
