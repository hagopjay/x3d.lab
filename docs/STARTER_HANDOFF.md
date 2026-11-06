# 00 MCP Starter: handoff (work in progress, 2026-11-05)

Goal (user): a simple Phase-1 notebook with no x3d.lab toolkit. It installs hagopjay or consortium x3d_mcp and puts it
through its paces up to a "semi-simple test" stage, with all 3 exposure options (none / cloudflared / ngrok) working on
both servers. It's for undergrads, so code is collapsed (cellView form) and the hints and insights are info-dense.

Files
- notebooks/src/00_starter.py  (source; edit this) -> notebooks/00_MCP_Starter.ipynb via `python tools/build_notebooks.py`
- tools/build_notebooks.py: a `<!-- collapsed -->` line in a markdown cell folds that section in Colab (metadata
  colab.collapsed_sections). Used for Advanced settings, Insights and Troubleshooting.

Fixes over the user's pasted cells
- NameError free_port: listeners/free_port/show_log/SERVER_TOKEN now live in cell 3 (the user's version had dropped them);
  removed the duplicate cell 3.
- Proxy rerun: waits for the port to free and times out on start (it used to spin forever).
- pyngrok is always installed. ngrok errors are mapped: 105 = not an authtoken (an API key does NOT work), 107 = invalid,
  108 = an agent is already running elsewhere.
- cloudflared: picks the arch, detects 429 rate limits; the self-check retries ~30 s for new public addresses and closes
  its test session.
- uv: --system on Colab, --python <exe> inside a venv. Your other secrets are not passed to the server.
- KEEP_TOKEN (Colab secret X3D_MCP_TOKEN) + NGROK_DOMAIN make client configs survive restarts.
- Cell 4 must always run (the tests go through the gatekeeper); its title says so.

Test stage: 5 helpers, 5a hello, 5b tool tour (describe_node, list_profiles, convert, autofix, step-by-step builder,
optional render), 5c 2 good + 16 broken scenes with expected layer (KNOWN_GAPS consortium: ROUTE before DEF, PBR
outside profile), 5d load (LOAD_SESSIONS x LOAD_CALLS_PER_SESSION), 6 scorecard JSON, 7a check a scene, 7b call any tool,
8 stop.

NOT YET DONE (next steps)
1. Execute the notebook with nbclient for SERVER in {hagopjay, consortium}, TUNNEL=none (sandbox: HOME=/tmp/...,
   kernel /home/claude/venv). Fix whatever breaks (check autofix output and the 5c verdicts on both servers).
2. cloudflared/ngrok can't really be verified in the sandbox (network allowlist, no ngrok token). The user tests
   those on Colab with their ngrok authtoken.
3. Commit, try a push (it has failed with 403 so far: the Claude GitHub App is not installed on jasonthurman-sys/x3d.lab),
   otherwise send a bundle. Add a row to the README table.
