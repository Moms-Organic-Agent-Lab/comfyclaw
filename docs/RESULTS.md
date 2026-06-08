# Results & method figures

This page collects the paper figures and benchmark results for ComfyClaw. The
main [`README.md`](../README.md) focuses on deploying and using the plugin; the
quantitative and qualitative evidence lives here.

> 📄 **Paper:** *An Agentic Harness for Skill-Evolving Image Generation
> Workflows* (Li, Liu, Chen, Wu, Liu, Zhou, Xie, Wu, Sun, 2026). See
> [Citing ComfyClaw](../README.md#citing-comfyclaw) or
> [`CITATION.cff`](../CITATION.cff).

---

## How it works

<p align="center">
  <img src="../assets/framework.png" alt="Overall framework of ComfyClaw — typed graph edits, VLM verifier, and skill-library evolution" width="100%">
</p>

<p align="center"><em>
<strong>Figure 1 · Overall framework of ComfyClaw.</strong> The agent edits
a ComfyUI workflow graph through three stage-gated phases —
<strong>Planning</strong>, <strong>Construction</strong>, and
<strong>Enhancement</strong> (1). The runtime renders a candidate image; a
region-level VLM verifier (2) returns requirement-level pass/fail labels
and a holistic detail score, which the harness combines into a scalar
reward. Below threshold, the failure feedback drives a refinement loop;
above threshold, the trajectory is committed and passed to the
skill-evolution module (3), which clusters successes and failures,
proposes mutations (<code>create / revise / reinforce / merge / delete</code>),
and commits only those that pass held-out validation.
</em></p>

<p align="center">
  <img src="../assets/refinement.png" alt="Iterative refinement walkthrough: a prompt is improved over successive verifier-guided iterations" width="100%">
</p>

<p align="center"><em>
<strong>Figure 2 · Iterative refinement.</strong> A single prompt is improved
over successive verifier-guided iterations, with region-level critiques driving
each repair.
</em></p>

---

## Quantitative

ComfyClaw is evaluated on four text-to-image benchmark splits — GenEval2,
DPG-Bench, OneIG-EN, and OneIG-ZH — using three agent models (Claude Sonnet
4.5, Qwen-3.6-35B-A3B, Gemma-4-E4B-it) and two image backbones
(Z-Image-Turbo, LongCat-Image). Headline numbers (Table 1 in the paper,
Soft-TIFA / VQAScore averaged):

| Setting | BASE | ComfyGEMS *(no skill evolution)* | **ComfyClaw** |
|---|---:|---:|---:|
| Claude Sonnet 4.5 + Z-Image-Turbo | 67.94 | 73.93 | **77.78** |
| Claude Sonnet 4.5 + LongCat-Image | 67.08 | 75.13 | **75.52** |
| Qwen-3.6-35B + Z-Image-Turbo | 63.84 | 70.23 | **78.62** |
| Qwen-3.6-35B + LongCat-Image | 65.05 | 65.51 | **76.34** |
| Gemma-4-E4B + Z-Image-Turbo | 60.84 | 60.84 | **65.01** |
| Gemma-4-E4B + LongCat-Image | 39.07 | 34.28 | **43.94** |

ComfyClaw posts the best average in **all six** agent–backbone settings,
improves over the verifier-only `ComfyGEMS` ablation by ≈ 4 points and
over the no-refinement `Base` by ≈ 10 points on average, and is
preferred by human annotators on a 2,400-image study (Table 2 in the
paper). Across the Claude-Sonnet runs the harness accumulates
**318 unique evolved skills (4,768 versions)**, and on dense /
compositional benchmarks these evolved skills account for **56–70 %**
of all skill reads.

---

## Qualitative

<p align="center">
  <img src="../assets/cherrypick.png" alt="Qualitative comparison: Base vs ComfyGEMS vs ComfyClaw on six prompts spanning five capability categories" width="100%">
</p>

<p align="center"><em>
<strong>Figure 3 · Qualitative comparison across methods on six prompts
spanning five capability categories.</strong> Each column is a prompt
(header shows the category and full description); rows are
<strong>Base</strong> (single-pass baseline), <strong>ComfyGEMS</strong>
(ComfyClaw without skill evolution), and <strong>Ours</strong>
(ComfyClaw, green border). ComfyClaw more reliably realises object
counts, spatial relations, scene-text accuracy, and fine-grained
attribute control. See <a href="REPRODUCING.md"><code>REPRODUCING.md</code></a>
for the exact commands used to produce these images.
</em></p>

---

## Reproducing

The full reproducibility guide lives at [`REPRODUCING.md`](REPRODUCING.md):
the exact ComfyUI / Python / `uv` versions, the checkpoints / LoRAs / VAEs to
download, the CLI commands for each headline experiment (with expected verifier
scores and iteration counts), and how to swap agent / verifier backends for the
multi-provider ablations.
