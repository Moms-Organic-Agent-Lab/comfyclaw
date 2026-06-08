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
