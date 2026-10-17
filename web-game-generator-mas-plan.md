# Web Game Generator — Architecture diagrams

Proposed multi-agent architecture for browser games, including mobile browsers.

## 1. System architecture

Three specialist agents share versioned project state through a durable orchestrator.

```mermaid
flowchart TB
    developer("Developer: prompt, switches, choices") --> api("Project API + versioned state")
    webmcp("WebMCP tools in browser: references + commands") --> api
    api --> orchestrator("Durable orchestrator")
    orchestrator --> designer("Designer / engine router")
    designer --> builder("Builder")
    builder --> tests("Build + browser gameplay tests")
    tests --> qa("QA agent")
    qa -->|Specific defects: bounded repair| builder
    qa -->|Checks pass| preview("Versioned playable preview: exact tested build")
    orchestrator -->|Decision needed| developer
```

[Download SVG](diagrams/system-architecture.svg)

## 2. Engine routing

Route by game requirements and developer preferences; validate explicit engine locks before building.

```mermaid
flowchart TB
    brief("Game brief + effective preferences + engine locks") --> router("Designer / engine router: requirements + capabilities")
    router -->|Interface-heavy| html("HTML/CSS + React: UI, text, turn-based")
    router -->|2D gameplay| phaser("Phaser: 2D, sprites, action")
    router -->|3D scenes| babylon("Babylon.js: 3D, cameras, models")
    html --> adapter("Selected engine adapter: pinned template + dependencies")
    phaser --> adapter
    babylon --> adapter
    adapter --> lifecycle("Builder → build → browser tests → QA")
```

[Download SVG](diagrams/engine-routing.svg)

## 3. Developer choices during generation

Save each decision, pause only affected tasks, and validate the answer against current project state.

```mermaid
flowchart TB
    choice("Unresolved choice: preferences do not settle it") --> card("Save decision card: question, options, recommendation")
    card -.-> independent("Continue independent work; pause only affected tasks")
    card --> answer("Developer chooses an option or gives a custom answer")
    answer --> validate{"Answer still valid for current project revision?"}
    validate -->|Yes| commit("Commit answer + preferences; create project revision")
    validate -->|No| refresh("Refresh question with updated context")
    refresh --> card
    commit --> resume("Resume dependent work using the accepted decision")
```

[Download SVG](diagrams/developer-decisions.svg)

---

[Standalone HTML diagrams](web-game-generator-mas-plan.html)
