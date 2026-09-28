# load_env.bash -- sourced, not run, by daily.bash and weekly.bash from the
# repo root.  Loads .env (DB password, API keys) into the environment.
#
# An existing environment variable wins; .env only fills in what is missing.
# That is the rule scrape/config.py and web/soogle_web/settings.py already
# follow.  These scripts used to `set -a; source .env`, which is the opposite
# rule, and the difference mattered: GH_TOKEN and the API keys are exported
# from ~wohl/.bashrc too, and the cron path (`su -l wohl`) runs .bashrc first.
# Each time the GitHub token was rotated in .bashrc and not in .env, the dead
# .env copy overrode the live one.  GitHub answered 401 from 2026-06-21 to
# 09-06, and again on 09-20 and 09-27, when the weekly run failed outright.

if [ -f .env ]; then
    # Keep xtrace off while values are handled, so tracing this file does not
    # copy every key into the periodic log that gets mailed out.  periodic.conf
    # does the same around its own secrets.
    case "$-" in *x*) _env_xtrace=1; set +x ;; *) _env_xtrace= ;; esac

    # Remember every .env name the environment already has...
    _env_keep=()
    for _env_name in $(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' .env); do
        if [ -n "${!_env_name+set}" ]; then
            _env_keep+=("$_env_name=${!_env_name}")
        fi
    done

    set -a
    # shellcheck disable=SC1091
    source .env
    set +a

    # ...and put those values back over whatever .env said.
    for _env_kv in "${_env_keep[@]}"; do
        export "$_env_kv"
    done

    unset _env_name _env_kv _env_keep
    if [ -n "$_env_xtrace" ]; then set -x; fi
    unset _env_xtrace
fi
