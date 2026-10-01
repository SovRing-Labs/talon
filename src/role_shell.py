"""Role Shell Generator — launches native interactive exoskeleton shells for Talon roles.
Replaces the broken curses multiplexer with an instantaneous, 100% typable interactive shell.
"""

import os
import sys
from pathlib import Path

ROLES_BANNER = {
    "commander": """\033[38;2;168;85;247;1m
╔═══════════════════════════════════════════════════════════════╗
║  👑 TALON COMMANDER EXOSKELETON — SOVEREIGN COCKPIT          ║
║  Status: $0 Token Cost | Mode: Strategic General             ║
║  Tools: doc <query> | dbr | memory | blackboard | andon      ║
╚═══════════════════════════════════════════════════════════════╝\033[0m
""",
    "dev": """\033[38;2;6;182;212;1m
╔════════════════════════════════════════════════════════════════╗
║  🔨 TALON DEV STUDIO — INTERACTIVE PAIR-PROGRAMMING          ║
║  Status: Fast Flow Workbench | Mode: Hands-On Solo Pilot     ║
║  Tools: doc <query> | andon | memory | git status            ║
╚════════════════════════════════════════════════════════════════╝\033[0m
""",
    "bugstomp": """\033[38;2;16;185;129;1m
╔═══════════════════════════════════════════════════════════════╗
║  🛡️ TALON BUGSTOMP LAB — ROOT-CAUSE ISOLATION & PROOF         ║
║  Status: Zero Speculation | Mode: Forensic Debugging         ║
║  Tools: andon trip | andon clear | doc <query> | memory       ║
╚═══════════════════════════════════════════════════════════════╝\033[0m
""",
    "builder": """\033[38;2;245;158;11;1m
╔═══════════════════════════════════════════════════════════════╗
║  🔨 TALON BUILDER — WORKTREE IMPLEMENTATION ENGINE           ║
║  Status: 4-Phase Swarm Protocol | Mode: Build Runner         ║
║  Tools: doc <query> | andon | test | git status              ║
╚═══════════════════════════════════════════════════════════════╝\033[0m
""",
    "oracle": """\033[38;2;139;92;246;1m
╔══════════════════════════════════════════════════════════════╗
║  🔮 TALON ORACLE — 3-LENS REVIEW & GATE VALIDATION           ║
║  Status: REL / REIM / ADV | Mode: Gatekeeper                 ║
║  Tools: done-gate <id> | doc <query> | memory                ║
╚══════════════════════════════════════════════════════════════╝\033[0m
""",
    "researcher": """\033[38;2;59;130;246;1m
╔═══════════════════════════════════════════════════════════════╗
║  🔬 TALON RESEARCHER — INTELLIGENCE & DISCOVERY SCOUT        ║
║  Status: Sub-15ms Knowledge Recall | Mode: RAG Analyst       ║
║  Tools: doc <query> | memory | blackboard                    ║
╚═══════════════════════════════════════════════════════════════╝\033[0m
""",
    "grunt": """\033[38;2;107;114;128;1m
╔══════════════════════════════════════════════════════════════╗
║  🧹 TALON GRUNT — FLEET HYGIENE & STEWARDSHIP                ║
║  Status: Process Reaping & Database Vacuuming                ║
║  Tools: vacuum | prune | log-rotate | doc <query>            ║
╚═════════════════════════════════════════════════════════════╝\033[0m
""",
}

PROMPT_COLORS = {
    "commander": r"\[\033[38;2;168;85;247;1m\]👑 [COMMANDER]\[\033[0m\] \[\033[38;2;6;182;212m\]\w\[\033[0m\] \$ ",
    "dev": r"\[\033[38;2;6;182;212;1m\]🔨 [DEV]\[\033[0m\] \[\033[38;2;168;85;247m\]\w\[\033[0m\] \$ ",
    "bugstomp": r"\[\033[38;2;16;185;129;1m\]🛡️ [BUGSTOMP]\[\033[0m\] \[\033[38;2;239;68;68m\]\w\[\033[0m\] \$ ",
    "builder": r"\[\033[38;2;245;158;11;1m\]🔨 [BUILDER]\[\033[0m\] \[\033[38;2;6;182;212m\]\w\[\033[0m\] \$ ",
    "oracle": r"\[\033[38;2;139;92;246;1m\]🔮 [ORACLE]\[\033[0m\] \[\033[38;2;16;185;129m\]\w\[\033[0m\] \$ ",
    "researcher": r"\[\033[38;2;59;130;246;1m\]🔬 [RESEARCHER]\[\033[0m\] \[\033[38;2;6;182;212m\]\w\[\033[0m\] \$ ",
    "grunt": r"\[\033[38;2;107;114;128;1m\]🧹 [GRUNT]\[\033[0m\] \[\033[38;2;6;182;212m\]\w\[\033[0m\] \$ ",
}


def launch_role_shell(role: str, session_name: str = None) -> None:
    role = role.lower()
    banner = ROLES_BANNER.get(role, ROLES_BANNER["commander"])
    ps1 = PROMPT_COLORS.get(role, PROMPT_COLORS["commander"])

    rc_dir = Path("/tmp/talon-roles")
    rc_dir.mkdir(parents=True, exist_ok=True)
    rc_file = rc_dir / f"bashrc-{role}.sh"

    rc_content = f"""# Auto-generated Talon Exoskeleton Bashrc for role: {role}
[ -f ~/.bashrc ] && source ~/.bashrc

export TALON_ROLE="{role}"
# role tools (compiled plugins, P4 de-orphan) first — same PATH talon-exec gives dispatched agents
export PATH="./roles/{role}/bin:bin:./.opencode/bin:${{PATH}}"
export PYTHONPATH="./src:./plugins:${{PYTHONPATH}}"

alias doc="python3 bin/fleet-docs"
alias fleet_doc="python3 bin/fleet-docs"
alias memory="python3 ./plugins/common/core-memory.py"
alias andon="python3 ./plugins/common/andon-guard.py"
alias andon_trip="python3 ./plugins/common/andon-guard.py trip"
alias andon_clear="python3 ./plugins/common/andon-guard.py clear"
alias dbr="python3 ./plugins/common/dbr-batch.py"
alias blackboard="python3 ./plugins/common/stigmergy-blackboard.py"
alias swarm="python3 bin/flight-plan"
alias git-workflow="bin/git-workflow"
alias task-radar="bin/task-radar"
alias stack-trace-nav="bin/stack-trace-nav"

# Preexec and precmd hooks for command logging
preexec() {{
    # Capture the command about to run
    local cmd="$1"
    # Skip the prompt machinery (PROMPT_COMMAND runs precmd/_talon_hud each prompt) and
    # the DEBUG trap's own re-entry, so only real user commands are logged.
    case "$cmd" in
      precmd*|_talon_hud*|__talon_rc=*|"") return 0 ;;
    esac
    export TALON_LAST_CMD="$cmd"
    # Get timestamp in ISO format
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%S")
    # Get the role from environment
    local role="${{TALON_ROLE:-unknown}}"
    # Write to events.jsonl as VALID json (printf keeps the double-quotes literal;
    # strip embedded quotes/backslashes from cmd so one bad command can't corrupt the line).
    local q='"'
    local safe_cmd="${{cmd//$q/}}"
    safe_cmd="${{safe_cmd//\\\\/}}"
    printf '{{"ts": "%s", "agent": "%s", "session": null, "verb": "COMMAND_EXEC", "task": null, "note": "- Executed: %s"}}\\n' "${{ts}}" "{role}" "${{safe_cmd}}" >> control-center/events.jsonl
}}
# Wire preexec into bash: without a DEBUG trap the function is never invoked.
trap 'preexec "$BASH_COMMAND"' DEBUG

precmd() {{
    # Exit code is passed in as $1 from PROMPT_COMMAND (captured before the DEBUG trap
    # can clobber $?). Check if the last real command failed and suggest fixes.
    local rc="${1:-0}"
    if [ "$rc" -ne 0 ] && [ -n "$TALON_LAST_CMD" ]; then
        echo -e "\033[38;2;255;100;100m[TALON ERROR] Command failed: $TALON_LAST_CMD\033[0m"
        echo -e "\033[38;2;100;255;100m[TALON SUGGESTION] Querying system-docs for fixes...\033[0m"
        suggestion=$(bin/fleet-docs search "$TALON_LAST_CMD" 2>/dev/null | head -3)
        if [ -n "$suggestion" ]; then
            echo -e "\033[38;2;100;200;255m$suggestion\033[0m"
        else
            echo -e "\033[38;2;200;200;200m[TALON] No suggestions found in system-docs.\033[0m"
        fi
    fi
    :
}}

# Talon Ambient HUD - displays fleet concurrency, WIP, Andon status, and memory RSS
_talon_hud() {{
    local active wip andon_state mem_rss
    # Get active fleet concurrency from fleet-metrics
    if active=$(cd . && bin/fleet-metrics 2>/dev/null | grep "Pending (active):" | awk '{{print $3}}'); then
        wip=$active  # WIP is same as active concurrency for this HUD
    else
        active=0
        wip=0
    fi
    
    # Get Andon status
    if [ -f /tmp/talon-andon.json ]; then
        andon_state=$(cat /tmp/talon-andon.json 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('state', 'clear'))" 2>/dev/null || echo "clear")
    else
        andon_state="clear"
    fi
    
    # Get memory RSS for current process
    mem_rss=$(ps -o rss= -p $$ 2>/dev/null | awk '{{printf "%dMB", int($1/1024)}}' 2>/dev/null || echo "0MB")
    
    # Determine Andon color: red if tripped, green if clear
    if [ "$andon_state" = "tripped" ]; then
        local andon_color="\033[91;1m"  # Bright red
    else
        local andon_color="\033[92m"    # Green
    fi
    
    # Print the HUD line
    echo -e "\033[38;2;0;255;255m[TALON HUD] Concurrency: ${{active}} | WIP: ${{wip}} | Andon: ${{andon_color}}${{andon_state}}\033[92m | RSS: ${{mem_rss}}\033[0m"
}}

# Capture exit code FIRST (before any prompt command clobbers $?), run precmd
# (exit-code repair) with it, then the HUD.
PROMPT_COMMAND="precmd $?; _talon_hud; ${{PROMPT_COMMAND:-:}}"

PS1='{ps1}'

# Clear and display native Talon Exoskeleton banner
printf "\\033c"
cat << 'TALON_BANNER_EOF'
{banner}
TALON_BANNER_EOF
"""

    rc_file.write_text(rc_content, encoding="utf-8")

    env = dict(os.environ)
    env["TALON_ROLE"] = role
    env["TALON_ACTIVE"] = "1"
    env["TERM"] = os.environ.get("TERM", "xterm-256color")
    os.chdir(".")
    # Auto-reconnect loop: respawn bash on abnormal exit (e.g., SIGHUP, SIGTERM due to PTY loss)
    while True:
        pid = os.fork()
        if pid == 0:
            # Child process: exec bash
            try:
                os.execvpe("/bin/bash", ["bash", "--rcfile", str(rc_file)], env)
            except Exception:
                os._exit(1)
        else:
            # Parent: wait for child
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
                # Clean exit, break loop
                break
            # Otherwise, abnormal exit; wait a bit and respawn
            time.sleep(1)
