---
title: Decision Making
description: Advanced decision making patterns
icon: material/source-branch
---

# Making Decisions with pick()

The `pick()` method provides a flexible way for agents to make decisions by selecting from available options. It can handle simple choices, agent selection, and complex options with custom labels.

## Basic Usage

### Simple Options

```python
agent = Agent(model="gpt-5")
decision = await agent.talk.pick(
    ["analyze", "plan", "execute"],
    task="What should we do next?"
)
print(f"Selected: {decision.selection}")  # e.g., "analyze"
print(f"Reason: {decision.reason}")       # explanation for the choice
```

### Labeled Options

You can provide explicit labels for better context:

```python
decision = await agent.talk.pick(
    {
        "A1 - Quick Scan": "Perform basic code analysis",
        "A2 - Deep Review": "Full code review with security focus",
        "A3 - Style Check": "Check code style and formatting",
    },
    task="How should we review this code?"
)
```

### Agent Selection

Pick can naturally select from teams or pools of agents:

```python
# From a team
expert = await agent.talk.pick(team, "Who should handle this task?")

# From a pool
specialist = await agent.talk.pick(pool, "Choose an expert for code review")

# From explicit list with custom labels
reviewer = await agent.talk.pick(
    {
        "Security Expert": security_agent,
        "Performance Guru": perf_agent,
        "Code Architect": arch_agent,
    },
    task="Who should review this critical component?"
)
```

!!! info
    In order for this mechanism to work, the name (and ideally also the description) of the agents should match
    their expertise and skillset.

## Return Values

The method returns a `Pick[T]` object containing:

- `selection`: The selected item (with proper type)
- `reason`: The reasoning behind the selection

```python
decision = await agent.talk.pick(options, task="Choose wisely")
selected: T = decision.selection  # Type-safe selection
reason: str = decision.reason    # Explanation for the choice
```

## Under the Hood

The `pick()` method works by:

1. **Option Processing**
   - Converts inputs to a map of labels to items
   - Gets descriptions using `to_prompt()` for each option
   - Handles different input types (sequences, mappings, teams, pools)

2. **Decision Making**
   - Creates a formatted prompt with options and descriptions
   - Uses structured output (`LLMPick`) for reliable selection
   - Maps the selected label back to the original item

3. **Type Safety**
   - Maintains type information through generics
   - Converts LLM's string-based selection to proper type
   - Provides appropriate typing for different input types

## Input Types

The method accepts:

- `Sequence[T]`: List of options
- `Mapping[str, T]`: Options with explicit labels
- `Team[TDeps]`: Team of agents
- `AgentPool`: Pool of agents

Where `T` is any type that can be converted to a prompt (implements `__prompt__` or is convertible by `to_prompt()`).

## Example: Complex Decision Making

```python
# Create specialized agents
analyzer = Agent(name="analyzer", description="Code analysis expert")
reviewer = Agent(name="reviewer", description="Security specialist")
architect = Agent(name="architect", description="System architect")

# Create decision maker
decider = Agent(
    model="gpt-5",
    system_prompt="You are an expert at task delegation"
)

# Make decision with custom labels and descriptions
decision = await decider.talk.pick(
    {
        "Analysis Team": [analyzer, reviewer],
        "Architecture Team": [architect],
        "External Review": "Hire external consultant",
    },
    task="How should we approach this critical system update?",
)

# Use the decision
match decision.selection:
    case list() as team:
        await team[0].run("Start the analysis...")
    case str():
        print("Arranging external consultation...")
```

The `pick()` method provides a natural way for agents to make decisions while maintaining type safety and providing clear reasoning for choices.

## Multiple Selections with pick_multiple()

When you need to select multiple options, use `pick_multiple()`. This method provides similar functionality to `pick()` but allows selecting multiple items with constraints.

### Basic Usage

```python
# Select multiple steps
steps = await agent.talk.pick_multiple(
    ["analyze", "plan", "test", "deploy", "monitor"],
    task="Which steps should we include?",
    min_picks=2,
    max_picks=3
)
print(f"Selected steps: {steps.selections}")
print(f"Reasoning: {steps.reason}")
```

### With Constraints

You can control the number of selections:

```python
team_members = await agent.talk.pick_multiple(
    {
        "Lead Developer": lead_dev,
        "Security Expert": security_expert,
        "Performance Guru": perf_expert,
        "Test Engineer": test_eng,
        "DevOps": devops_eng,
    },
    task="Who should be on the core team?",
    min_picks=2,    # At least 2 members
    max_picks=4,    # But no more than 4
)
```

### Return Value

The method returns a `MultiPick[T]` object containing:

- `selections`: List of selected items (with proper types)
- `reason`: The reasoning behind the selections

```python
result = await agent.talk.pick_multiple(options, task="Select components")
selected: list[T] = result.selections  # Type-safe list of selections
reason: str = result.reason           # Explanation for choices
```

### Parameters

- `selections`: Same as `pick()` - sequence, mapping, team, or pool
- `task`: Description of what to select
- `min_picks`: Minimum number of required selections (default: 1)
- `max_picks`: Optional maximum number of selections
- `prompt`: Optional custom selection prompt

### Example: Team Formation

```python
# Create specialized agents
analyzer = Agent(name="analyzer", description="Code analysis expert")
reviewer = Agent(name="reviewer", description="Security specialist")
architect = Agent(name="architect", description="System architect")
tester = Agent(name="tester", description="Testing expert")

# Select team with custom labels
team = await agent.talk.pick_multiple(
    {
        "Core - Analysis": analyzer,
        "Core - Architecture": architect,
        "Support - Security": reviewer,
        "Support - QA": tester,
    },
    task="Form a team for this critical project",
    min_picks=2,
    max_picks=3,
)

# Use the selections
for member in team.selections:
    await member.run("Initialize project analysis...")
```

### Comparison with pick()

- `pick()`: Single selection, clear choice needed
- `pick_multiple()`: Multiple selections, flexible constraints
- Both maintain type safety and provide reasoning
- Both work with all input types (sequences, mappings, teams, pools)

Choose `pick_multiple()` when you need to:

- Select multiple team members
- Choose multiple approaches/steps
- Form groups or subteams
- Make decisions with multiple components
