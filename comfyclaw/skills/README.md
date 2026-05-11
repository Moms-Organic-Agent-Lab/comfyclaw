# ComfyClaw built-in skills

This folder ships with ComfyClaw. Every subdirectory is a single skill
the agent can load on demand to handle a specific kind of ComfyUI task.

If you opened the ComfyClaw panel and clicked the **Skills** tab, this is
where the entries with the 📦 **built-in** pill came from.

## What is a skill?

A skill is a self-contained markdown document the agent reads when its
description matches the user's intent. Each skill teaches one specific
technique — "how to do photorealistic photos", "how to inpaint a masked
region", "how to wire a workflow without validation errors". The agent
loads only the relevant skill(s) for a given task instead of carrying
the whole library in its context.

Every skill is a folder named in `lowercase-hyphenated` form with a
single `SKILL.md` file (and optionally supporting assets). The first
~500 characters of the `description` field decide whether the agent
loads the skill for a request, so descriptions are written to mention
the literal trigger words a user is likely to say.

## Skill format

```markdown
---
name: my-skill
description: >-
  One-paragraph trigger description. Mention the user-facing words and
  phrases that should fire this skill ("photorealistic", "inpaint",
  "wrong count", "exception_during_inner_validation", …). 200–600 chars.
license: MIT
compatibility: ComfyClaw agent — what nodes / tools are required.
allowed-tools: optional space-separated tool names the agent may call
metadata:
  author: you
  version: "0.1.0"
  provenance: >-
    Optional — cite an external source if the content was distilled
    from a paper, dataset, or another open library.
---

Body of the skill. Action-oriented. Tables, recipes, decision rules,
concrete examples. Skip long preamble — the agent only loads the file
when it's already decided to use the technique.
```

Recommended sections in the body:

1. **Detection** — when this skill fires (verifier signals, user phrases).
2. **Recipe(s)** — numbered steps or a fenced code block of the change.
3. **Decision table** — symptom → action mapping for common variants.
4. **Gotchas / failure modes** — what goes wrong if the recipe is misapplied.

## The catalog (as of v0.2.0)

| Skill | Lines | What it does |
|---|---:|---|
| `controlnet-control`    |  82 | Add a ControlNet branch to enforce structural / spatial constraints |
| `counting-prompts`      | 160 | Strategy for counts, multi-subject, and unusual-attribute prompts |
| `creative`              |  47 | Sampler + prompt tags for vivid, concept-art-style output |
| `dreamshaper8-lcm`      | 145 | DreamShaper 8 LCM model configuration (low-step, low-CFG) |
| `high-quality`          |  52 | Raise output fidelity via prompt tokens + sampler params |
| `hires-fix`             |  51 | Second-pass upscale-and-refine for detail recovery |
| `inpainting`            | 141 | Region-only editing with masks (`VAEEncodeForInpaint` vs `SetLatentNoiseMask`) |
| `lora-enhancement`      |  71 | Inject LoRA adapters to fix defects the base model cannot |
| `negative-prompts`      | 169 | Build defect-targeted negative prompts; family-specific gotchas |
| `photorealistic`        |  51 | Camera-realistic prompts + sampler tuning |
| `prompt-artist`         |  67 | Rewrite flat prompts into vivid artistic language |
| `qwen-image-2512`       | 192 | Qwen-Image-2512 (Alibaba) model configuration |
| `regional-control`      |  71 | Apply separate prompts to distinct image regions |
| `sampler-selection`     | 133 | Choose `sampler_name` + `scheduler` for the active checkpoint family |
| `skill-creator`         | 485 | Meta-skill: build, test, and benchmark new skills |
| `spatial`               |  45 | Rewrite prompts to encode spatial relationships explicitly |
| `text-rendering`        |  58 | Restructure prompts for accurate in-image text |
| `workflow-builder`      | 440 | Build a complete txt2img / SDXL / Flux / DiT workflow from scratch |
| `workflow-guardrails`   | 175 | Preflight wiring / enum / parameter checks for validation safety |

Style + quality skills (`photorealistic`, `creative`, `high-quality`,
`prompt-artist`) layer cleanly: pick one rewriter (prompt-artist) and
one parameter tuner (creative / photorealistic / high-quality) per
request. The reasoning skills (`counting-prompts`, `negative-prompts`,
`workflow-guardrails`) are universal — they apply regardless of the
chosen style.

## Where they're loaded from

The SkillsRegistry scans three roots in this order (later overrides
earlier on name collisions):

| Source label  | Path                                         | Notes |
|---------------|----------------------------------------------|-------|
| `builtin`     | `comfyclaw/skills/` inside the package       | This folder. Read-only at runtime. |
| `user`        | `~/.comfyclaw/skills/`                       | User-imported skills (override via `$COMFYCLAW_USER_SKILLS_DIR`). |
| `extra`       | `--skills-dir <DIR>` or `$COMFYCLAW_SKILLS_DIR` | Optional extra root. |

Enable/disable state lives in `~/.comfyclaw/skills_state.json` and
survives reinstall. On startup the server logs the resolved roots:

```
[SkillsRegistry] loaded 19 skills from: builtin=…/comfyclaw/skills, user=/Users/…/.comfyclaw/skills
```

## Adding your own skills

Three import paths, all available from the **Skills** tab in the panel:

- **📁 Folder** — point at any directory containing a `SKILL.md`. The
  registry copies it into the user root.
- **🗜 .zip** — upload a zip whose top-level folder contains `SKILL.md`.
- **🌐 Git** — clone a public repo. Optional branch / tag / ref.

You can also write to `~/.comfyclaw/skills/<your-skill>/SKILL.md`
directly and hit the **↻** refresh button in the Skills tab.

Run the `skill-creator` skill itself to scaffold a new skill,
benchmark its triggering accuracy against the existing catalog, and
iterate on its description.

## Licensing

Unless a skill's frontmatter says otherwise, the built-in skills are
**MIT-licensed**. The `qwen-image-2512` skill carries an Apache-2.0
header — check each file's `license:` field before redistributing.

Skills marked with a `metadata.provenance:` field were distilled from
an external source; the provenance line cites the upstream work.
Please preserve provenance lines if you adapt those skills further —
they're how credit flows back to the original authors.

## Contributing

PRs adding new skills are welcome. A good submission:

- Targets a real, recurring user need that isn't already covered.
- Has a description rich in literal trigger words (200–600 chars).
- Includes a decision table or recipe block, not just prose.
- Is tested end-to-end at least once by running the agent and watching
  the skill fire (use `skill-creator`'s `evaluate` workflow if unsure).
- Carries `license:`, `metadata.author:`, and `metadata.version:` in
  the frontmatter.

If you're not sure where your skill fits, list it in the catalog table
above with a one-line summary and we'll triage from there.
