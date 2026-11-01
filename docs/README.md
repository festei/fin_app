# Documentation

| Folder | What it holds |
| --- | --- |
| [`specs/`](specs/README.md) | One spec per plan that was executed, in the order they were built: context, decisions, design, acceptance criteria, what review changed, known limits. |
| [`architecture/`](architecture/README.md) | Diagrams of the whole system end to end: containers, repository layout, data model, pipelines, scoring graph, alerts, sandbox, API and UI, deployment and security. |

`CLAUDE.md` at the repository root remains the dense, always-current reference for working in the code;
these documents explain *why* things are the way they are and *how the parts fit together*.
When the two disagree, the code and `CLAUDE.md` win, and the document should be fixed.

Diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub.
