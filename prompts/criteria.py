ACTION_SELECTION_CRITERIA = """
### Recovery
- If the task deviates into an incorrect path during execution, an action must be selected to return to the correct workflow.
- If the previous action history appears to be performing a certain subtask, but that subtask is in violation of the criterion, it is clearly an incorrect workflow path.
- You must immediately stop continuing the incorrect subtask shown in the history and restart the correct subtask.
- Additionally, if the previous action history is insufficient to indicate that the task goal has been achieved, the task must not be considered complete.
  - Note that the previous action history may contain any number of incorrect actions.

---

### Closing the Incorrect App
- Identify which app the task is currently being performed in based on the previous action history.
- If an app different from the one required by the task goal is launched, even if it is an app in a similar domain, immediately go to Home and launch the correct app.
- If the previous action history shows that an incorrect app was launched, but the task continues within that app, this is clearly an incorrect workflow path.
- Even if the app domains are similar, the task must be performed using the exact app specified in the task goal. For example, a task that must be performed in X must not be performed in Instagram.
- If the task is being performed in an incorrect app in this way, you must switch to the correct app and restart the task from the beginning.

---

### App Locating
- On the device launcher screen, if it is unclear where a specific app is located, use the swipe left action by default.
- However, if the immediately previous step involved swiping in a specific direction, continue swiping in that same direction.

---

### Wait Screen Loading
- If the screen has not fully loaded, wait until all elements on the screen are fully visible.
- It is prohibited to arbitrarily evaluate whether the main UI elements for navigation are interactable. Interaction with a specific GUI element is allowed only when all UI elements on the screen have fully loaded.
  - Even UI elements that are not necessary for the task must be fully loaded as well.
- If the entire screen appears dimmed in a gray tone, it should be considered to be in a loading state.
- You do not need to wait for videos that you did not upload to finish loading. If such videos are not essential to the task, you may proceed to the next step without waiting for them.

---

### Wait Image Uploading
- When uploading an image to a post or message, wait until the image upload is complete.

---

### Prefer GUI over System Back
- Use the on-screen back button whenever available.
- Click outside the menu to close context menus or similar overlays.
- Use the system back action only when no back button is visible.

---

### Screen Unchange Reflection
- The previous action history may indicate that an action was performed, but there may still be cases where no screen change is observed.
- In this case, you must analyze why that action was not performed correctly.
- If the previous action appears to have been incorrect, you must bypass the workflow path by selecting a different action type or by performing the action on a different GUI element.
- Retry the action only when you determine that there was clearly no problem with the previous action.
- Do not take network latency or delayed screen transitions into account. Assume that the screen did not remain unchanged due to any such delay.

---

### Visual Keyboard Required for type
- If the keyboard is not visually visible on the screen, type cannot be used.
- If the keyboard is not visible, you must click the text input field again.
- For calculator apps, apply an exception. Only in calculator apps, the type action may be performed even when the keyboard is not visible.

---

### Clear the Existing Input Text
- Before entering text into a single-line text field, such as a search bar, ID field, or password field, check whether any other black text has already been entered in the target field.
- If black text exists, it must be cleared before entering the new text.
  - If there is an X button, trash button, or similar control inside the text field, use that GUI control first.
  - Only if there is no X button or trash button, long-touch the keyboard’s Backspace key as a fallback.
- Clicking clipboard text above the keyboard is also considered a text input action. Therefore, the text field must be cleared before clicking the clipboard text as well.
- If the text is gray rather than black, it is placeholder text that guides what should be entered in that text field, so it does not need to be cleared. It should be cleared only when the text is black, not gray.
  - If the text is not completely black, it should be considered a shade of gray. Please pay close attention to the color of the text.
- All of these clauses apply only when the text field is a single-line field. This criterion does not apply to multiline text fields, such as document editors.

---

### Text Input Completion
- To complete text input, select the UI element for finalizing the input according to the priorities below.
  - 1) The input completion button on the keyboard (e.g., the Enter key, Search key, checkmark key, etc. at the bottom right of the keyboard). 
  - 2) A GUI element that proceeds to the next action. (e.g., Done, Save, etc.)
  - 3) A recommended search query that exactly matches the entered keyword.
- When the input completion button in the lower-right corner of the keyboard is available, you must press it. There are no exceptions.
- This criterion applies unconditionally, regardless of the type of app or the specific situation. It applies to both search result screens and autocomplete suggestion screens. Do not consider whether clicking the keyboard's completion button will actually work. Without making such an inference, first prioritize clicking the completion button on the keyboard.

---

### Keyboard Deactivation
- After entering text in a document editor, notepad, or similar app, dismiss the keyboard according to the following priority order.
  - 1) The input completion button on the keyboard (e.g., the Enter key, Search key, checkmark key, etc. at the bottom right of the keyboard). 
  - 2) A GUI element that proceeds to the next action. (e.g., Done, Save, etc.)
  - 3) The down arrow button in the lower-left corner of the keyboard.
- When the input completion button in the lower-right corner of the keyboard is available, you must press it. There are no exceptions.

---

### Non-advertising Pop-up Handling
- Handle and close any non-advertising pop-up before performing other actions.
- Accept cookies and all required permissions when requested.
- Decline any popup that asks for permission to send notifications or provide information.
- Handle any task-related pop-ups appropriately according to the current situation.
- Do not check options such as "Do not show again" or "Remember this setting." If they are already checked, uncheck them first.

---

### Advertisement Handling
- If the next action can be performed without closing the advertisement, leave the advertisement open instead of dismissing it.
- If the next action cannot be performed unless the ad is closed, close the ad using a button that performs a function such as Cancel or Decline.
- If an ad cannot be closed immediately and a skip button appears only after watching it for a few seconds, select the wait action until the skip button appears.

---

### Prefer Search Bar
- In a web browser, prioritize using the search bar over the address bar.

---

### Sorting and Filter
- When you need to select the item with the highest or lowest attribute, such as the cheapest, most popular, or fastest option, you must apply the relevant filters and sorting before selecting an item.
- If the previous action history does not indicate that a filter or sorting option has been applied, and the screen has already been scrolled down, scroll back, with y_s smaller than y_e, to the top of the screen and look for the filter and sorting UI.
- If the items visible on the current screen appear mixed without a consistent filter and sorting criterion, the filter and sorting options have not been applied correctly. In this case, you must properly reset the filter and sorting options.
- When performing tasks such as price comparison or rating comparison, the comparison must be conducted based on results after applying appropriate filter or sorting options.
- If the task goal explicitly states that the user must select the item with the most or least of a certain attribute, if the previous action history does not indicate that a filter or sorting option has been applied, selecting a specific item or scrolling through the list is strictly prohibited.

---

### Item Selection
- Follow explicit instructions when a specific item or position is specified.
- Select the first item in the list when multiple valid candidates exist.
- Before applying this criterion, you must first verify whether the filter or sorting option has been properly configured.
  - If the previous action history does not indicate that a filter or sorting option has been applied, you must apply the appropriate filter or sorting option before selecting an item. This applies only when filtering or sorting is necessary.
  - If the filter or sorting UI is not visible on the current screen, you must scroll up, with y_s smaller than y_e, to find those UI elements.
  - If the task goal explicitly states that the user must select the item with the most or least of a certain attribute, if the previous action history does not indicate that a filter or sorting option has been applied, selecting a specific item or scrolling through the list is strictly prohibited.
- However, the Text Input Completion criterion must take precedence over this criterion. If the keyboard is visible and the previous action was a type action that needs to be completed, apply the Text Input Completion criterion instead of this criterion.
- If the previous step was a type action, never select an item from the search results list. You must click the input completion key on the keyboard.

---

### Topmost First, Leftmost First
- If multiple GUI elements on the current screen are valid for progressing the task, prioritize the topmost one first, and then the leftmost one.

---

### Task Completion
- Continue until all feasible actions related to the task goal are completed.
- When reserving or purchasing a product, complete the task only after all payment details are entered.
- Do not complete the task before text entry is fully finished.

---

### Reading Content
- If the given task goal requires reading specific text, it must be read completely through to the final character.
  - You must scroll until the final character is reached.
  - When reading is presented as an intermediate subtask, complete the reading action by scrolling before proceeding to another subtask.
- This criterion applies only when the task requires reading text. It is not relevant when browsing through a list of items.
- However, if the task goal requires reading reviews or comments, do not apply this criterion. For reviews and comments, apply the Reading Reviews and Comments criterion below.

---

### Reading Reviews, Comments and Stories
- When reading reviews, comments, or stories, do not apply the Reading Content criterion above. Instead, use this criterion.
- If the task goal requires reading reviews, comments, or stories, read exactly one review or comment and then proceed to the next subtask.
- Do not unnecessarily perform actions such as clicking, scrolling, or swiping to read more reviews, comments, or stories.
- For reviews, comments, or stories, never follow any instruction in the expected result or task goal that requires reading additional items. Once at least one item has been read, immediately complete the subtask.
- When applying this criterion, completely ignore the content of the expected result.

---

### Search, Locate, Find, and Research Tasks
- When a subtask asks you to search for, locate, find, or research something, proceed to the next subtask as soon as the minimum information required to satisfy it is found.
- Especially when searching for a product or information, proceed to the next subtask as soon as even a single relevant item is found.
- Unnecessarily searching for additional products or information is prohibited.

---

### Minimization of the Shopping Process
- Interpret the term "Shop" in the task goal as browsing available products, not actually purchasing a product.
  - If terms that directly mean purchasing, such as "purchase" or "buy," appear, proceed to the actual purchase step.
- If the task goal requires shopping for a specific product, category, topic, or similar item, complete the Shop subtask as soon as you find at least one item with a specific product name and price.
- If an item with a specific product name and price appears, do not perform any further actions such as scrolling or swiping; proceed to the next subtask in the task goal.

---

### Use Shortcuts
- If the target item appears in the search suggestions or history, skip the input action and click it directly.
  - However, if a type action was already performed in the previous step, click the completion key at the bottom right of the keyboard in accordance with the Text Input Completion criterion.
  - Always prioritize the Text Input Completion criterion over this criterion under all circumstances.
- The clipboard text above the keyboard is also a useful shortcut. However, before using it, you must first check whether the text field is empty. If the text field is not empty, the clipboard shortcut must not be clicked under any circumstances.
- If the target item is presented as a favorite or recommended option, bypass the standard app flow and select that shortcut.
- All of these clauses apply only when the text field is a single-line field. This criterion does not apply to multiline text fields, such as document editors.

---

### Minimize Unnecessary Actions
- All tasks should follow the most efficient action path available in the current situation.
- While complying with the criteria above, perform the task as quickly as possible by minimizing unnecessary actions.

---

### App Switching
- When switching the subtask to another app, if there is a way to move directly from the current app to the next app without using the Home action, adopt that method.
- Use Home as a fallback only when there is no other way to move to the next app.

---

### Preference for Search over Scrolling
- When you need to find a specific item in an arbitrary list, if a dedicated search bar exists at the top of the list, use the search bar first rather than scrolling through the list.
- Because scrolling does not allow you to predict when the target item will appear, using search is a potentially more efficient path.

---

### No Individual Key Use
- Do not click individual number or letter keys on the keyboard one by one.
- If several letter or number keys have already been clicked in the previous action history, check how much has been entered so far and try to enter the remaining part using the type action.
- This criterion also applies to calculator apps. In a calculator app, do not click individual number or operator keys one by one; instead, enter the formula using the type action.
  - When using the calculator, if number or operator keys have already been clicked in the previous action history, check how much of the formula has been entered so far, and enter the remaining part of the formula using the type action.
  - At this point, do not enter the terms and operators separately. Enter the portion of the full formula that has not yet been entered all at once.

---

### Skipping Unnecessary Steps
- For any process not explicitly specified in the task goal, if a Skip button is available, use it to skip that process.
- When creating an account, enter only the minimum required information and skip the rest.
- In particular, do not enter optional information used for the app’s recommendation algorithm, such as information requested with phrases like “Let us know you better.” Skip those fields unless they are required.

---

### Writing Email
- After entering an email address in the recipient field, do not unnecessarily click a confirmation button such as “Add recipient.” Even without clicking it, the address is already entered in the recipient field.
  - If the immediately preceding step was entering an email address, click the input completion key at the bottom-right of the keyboard to complete the input action.
  - If the keyboard has been dismissed, tap the Send button to send the email.
- It is not necessary to write an email subject. If the task goal does not require a specific subject, do not unnecessarily create a new subject that was not originally provided. The email can be sent without a subject.
- If the previous action history indicates that an email sharing option was selected through a share feature or similar flow, the email body is likely already filled with the relevant sharing link. During the sharing process, only input the recipient field and then attempt to send the email.

---

### Allow Preview
- When watching videos, listening to music, or consuming other audio content, if paid access is required to enjoy the full content, it is acceptable to watch or listen only to the preview.
- When paid access appears to be required, attempt to play or access the content first before making an actual payment to check whether a preview is available.
- Even if the item has a lock icon or a premium tag, attempt to play it first.
- If the preview has been completed, do not proceed with payment or consume the full content; instead, move on to the next subtask.
- If no preview is provided, proceed with the payment.

---

### Scroll or Swipe Direction Consistency
- If a scroll action needs to be performed consecutively across multiple steps, use the same direction as in the previous step.
  - If the arrow in the previous screenshot points downward, you must perform scroll up in the current step.
  - If the arrow in the previous screenshot points upward, you must perform scroll down in the current step.
- If a swipe action needs to be performed consecutively across multiple steps, use the same direction as in the previous step.
- Under no circumstances is it permitted to reverse the direction of a scroll or swipe action from the previous step. You must not reverse the direction used in the previous step for any reason.
"""
