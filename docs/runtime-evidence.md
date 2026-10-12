# NBlood load smoke evidence

On 2026-10-12, BloodForge ran the generated map
`data/artifacts/89/89c5a0546b4f2bfbe6ec6bdfc5cf8f4e2e9b50ecf1ad3cd5623b39bb97ee8284.map`
through the local NBlood load smoke. The run completed with status `initialized`:
NBlood emitted the autoexec, game-loop, and map-initialization markers and
remained alive for the five-second observation window.

The executable was `D:/Games/DOS/llmapper/reference/blood/nblood.exe`, version
`r14433[codex/llmapper-autonomous-playtest-bot]-f88c4f189`, SHA-256
`832f4ef43c9ffee2a03b1a59bce310f2909870c665ead76126aa141c6dbf25af`. The MAP
SHA-256 was
`89c5a0546b4f2bfbe6ec6bdfc5cf8f4e2e9b50ecf1ad3cd5623b39bb97ee8284`. Before
and after hashes matched for the input map, executable, and all 554 files under
the reference Blood asset directory.

The auditable run directory is
`data/runtime/nblood-89c5a0546b4f-1791839151522966400/`; it contains the local
MAP copy, absolute-path config, private game-asset copy, command output, engine
log, and `report.json`. NBlood writes its texture cache into the game-data
search directory, so the runtime wrapper staged a private copy of the
read-only reference assets under `data/` and passed that copy with `-game_dir`.
This kept all engine writes inside BloodForge's data directory.

This result demonstrates native map loading and engine initialization only. It
does not verify player spawning, stair traversal, visual fidelity, or
playability; those claims remain false in the machine-readable report.
