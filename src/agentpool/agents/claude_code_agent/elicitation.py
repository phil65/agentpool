from typing import TYPE_CHECKING, Any

from clawd_code_sdk import PermissionResultAllow, PermissionResultDeny

from agentpool import log


if TYPE_CHECKING:
    from clawd_code_sdk import PermissionResult, ToolPermissionContext
    from clawd_code_sdk.models import AskUserQuestionInput

    from agentpool.agents.context import AgentContext

logger = log.get_logger(__name__)


async def handle_clarifying_questions(
    agent_ctx: AgentContext[Any],
    input_data: AskUserQuestionInput,
    context: ToolPermissionContext,
) -> PermissionResult:
    """Handle AskUserQuestion tool - Claude asking for clarification.

    The input contains Claude's questions with multiple-choice options.
    We present these to the user and return their selections.

    Users can respond with:
    - A number (1-based index): "2" selects the second option
    - A label: "Summary" (case-insensitive)
    - Free text: "jquery" or "I don't know" (used directly as the answer)
    - Multiple selections (for multi-select): "1, 3" or "Summary, Conclusion"

    Question format from Claude:
    {
        "questions": [
            {
                "question": "How should I format the output?",
                "header": "Format",
                "options": [
                    {"label": "Summary", "description": "Brief overview"},
                    {"label": "Detailed", "description": "Full explanation"}
                ],
                "multiSelect": false
            }
        ]
    }

    Response format:
    {
        "questions": [...],  # Original questions passed through
        "answers": {
            "How should I format the output?": "Summary",
            "Which sections?": "Introduction, Conclusion"  # Multi-select joined with ", "
        }
    }

    Args:
        agent_ctx: Agent context
        input_data: Contains 'questions' array with question objects
        context: Permission context

    Returns:
        PermissionResult with updated input containing user's answers
    """
    questions = input_data["questions"]
    if not questions:
        return PermissionResultDeny(message="No questions provided")
    # Collect answers from the user
    answers: dict[str, str] = {}
    for question_obj in questions:
        question_text = question_obj["question"]
        header = question_obj["header"]
        options = question_obj["options"]
        multi_select = question_obj["multiSelect"]
        if not question_text or not options:
            continue

        # Format the question for display
        formatted_question = f"{header}: {question_text}" if header else question_text
        option_labels = [opt["label"] for opt in options]
        option_descriptions = {opt["label"]: opt["description"] for opt in options}
        # Get user's answer via input provider
        # Build a display string showing the options
        options_display = "\n".join(
            f"  {i + 1}. {label}"
            + (f" - {option_descriptions[label]}" if option_descriptions[label] else "")
            for i, label in enumerate(option_labels)
        )
        full_prompt = f"{formatted_question}\n\nOptions:\n{options_display}\n\n"
        if multi_select:
            full_prompt += (
                "(Enter numbers separated by commas, or type your own answer)\nYour choice: "
            )
        else:
            full_prompt += "(Enter a number, or type your own answer)\nYour choice: "
        try:
            # Use input provider to get user response
            input_provider = agent_ctx.get_input_provider()
            user_input = await input_provider.get_input(context=agent_ctx, prompt=full_prompt)
            if user_input is None:
                return PermissionResultDeny(message="User cancelled question", interrupt=True)
            # Parse user input - handle numbers, labels, or free text
            # This follows the SDK pattern: try numeric -> try label -> use free text
            if multi_select:  # Split by comma for multi-select
                selections = [s.strip() for s in user_input.split(",")]
            else:
                selections = [user_input.strip()]
            selected_values: list[str] = []
            for selection in selections:
                if selection.isdigit():  # Try to parse as number first
                    idx = int(selection) - 1
                    if 0 <= idx < len(option_labels):  # Valid number - use the option's label
                        selected_values.append(option_labels[idx])
                    else:  # Invalid number - treat as free text
                        selected_values.append(selection)
                else:  # Try to match label (case-insensitive)
                    matching = [i for i in option_labels if i.lower() == selection.lower()]
                    if matching:  # Matched a label - use it
                        selected_values.append(matching[0])
                    else:  # No match - use as free text
                        selected_values.append(selection)

            # Store answer - join multiple selections with ", "
            # Use free text directly if provided (not "Other")
            answers[question_text] = ", ".join(selected_values)

        except Exception as e:
            logger.exception("Error getting clarifying question answer")
            return PermissionResultDeny(message=f"Error collecting answer: {e}", interrupt=True)
    # Return the answers to Claude
    return PermissionResultAllow(updated_input={"questions": questions, "answers": answers})
