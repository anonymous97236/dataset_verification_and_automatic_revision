ACTION_SPACE = """
### terminate('success'):
- If the given task goal is judged to have been fully achieved on the current screen, select this action to complete the episode.
  - Use the given expected result as a reference when assessing whether the task goal has been achieved, but do not rely on it completely.
  - If the given expected result violates any criterion, it must be considered incorrect and disregarded.
  - If the task goal already appears to have been fully achieved, regardless of the expected result, disregard the expected result and select terminate.

### terminate('failure'):
- If the test cannot proceed further on the current screen, and no action performed on this screen can lead toward achieving the task goal, select this action to abort the episode.
- If it is possible to return to a previous screen and resume the task through an alternative path, you should select back or click the back button rather than treating it as an abnormal termination.

### click(x, y):
- An action that clicks the (x, y) point in the current screenshot.

### scroll((x, y_s), (x, y_e)):
- An action to scroll a specific area either upward or downward.
- (x, y_s) represents the starting point of the scroll.
- (x, y_e) represents the ending point of the scroll.
- Since scrolling operates only in the vertical direction (up or down), the x values of the two coordinates must be identical.
- For Scroll actions, the actual interaction direction is the opposite of the notation. Pay close attention to the following interpretation of directions.
  - Scroll Down: To reveal content hidden at the bottom of the area, you must scroll from bottom to top; that is, y_e should be smaller.
  - Scroll Up: To reveal content hidden at the top of the area, you must scroll from top to bottom; that is, y_s should be smaller.
- The scroll must be performed precisely within the target area. Do not select points that extend excessively outside the area.

### swipe((x_s, y), (x_e, y)):
- An action to swipe a specific area to the left or right.
- (x_s, y) represents the starting point of the swipe.
- (x_e, y) represents the ending point of the swipe.
- Since swiping operates only in the horizontal direction (left or right), the y values of the two coordinates must be identical.
- For Swipe actions, the actual interaction direction is the same as the notation. Pay close attention to the following interpretation of directions.
  - Swipe Right: To reveal content hidden at the left of the area, you must scroll from left to right; that is, x_s should be smaller.
  - Swipe Left: To reveal content hidden at the right of the area, you must scroll from right to left; that is, x_e should be smaller.
- The swipe must be performed precisely within the target area. Do not select points that extend excessively outside the area.

### type('text'):
- An action that enters text while the keyboard is visible.
- Use the exact same capitalization, line breaks, and spacing as in the task goal.
- Do not arbitrarily make up any text parameters that cannot be verified from the given task goal, screenshot, or previous action history.

### back:
- An action that moves to the previous screen.
- This action uses the mobile operating system's back function. Note that it does not involve clicking a button displayed in the GUI.
- This is primarily used to navigate back to a previous screen within the same app.

### wait(t):
- An action that waits for t seconds.
- If the given screenshot and input data do not indicate exactly how many seconds to wait, use the default wait time of 3 seconds.
- Otherwise, if the screenshot or input data clearly allows you to infer how many seconds to wait, select that duration.

### long_press(x, y):
- An action that presses and holds at the (x, y) point.
- Use this when you need to long-press specific content to open a context menu, or when you need to press and hold a specific button.

### home
- An action that closes current app screen and returns to the device’s home screen.

### drag((x1, y1), (x2, y2))
- An action that drags the object from (x1, y1) to (x2, y2).
- It is used to move the position of an object.
- Examples of objects include sliders, reorder handles, cropped areas, text selection markers, panel handles, and various other elements.
"""

OUTPUT_FORMAT = """
- Action Type: You must select exactly one of terminate, click, scroll, swipe, type, back, wait, long_press, home, drag and output only the action type name without any additional explanation.
- Action Parameter:
  - (x, y) for click and long_press
  - ((x, y_s), (x, y_e)) for scroll
  - ((x_s, y), (x_e, y)) for swipe
  - 'text' for type
  - t for wait
  - 'success' or 'failure' for terminate
  - None for back and home
  - ((x1, y1), (x2, y2)) for drag
"""
