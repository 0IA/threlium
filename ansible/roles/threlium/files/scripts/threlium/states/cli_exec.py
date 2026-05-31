#!/usr/bin/env python3
"""cli_exec → ingress@localhost: исполнение команды в transient ``systemd-run --scope`` (ARCHITECTURE §6).

Capability-профиль = хвост ``X-Threlium-Capabilities`` на входящем письме.
Ресурсные лимиты (``MemoryMax``, ``CPUQuota``, ``TasksMax``) из ``Config``
(env: ``THRELIUM_CLI_EXEC_*``). ``cli_exec`` только читает capabilities.

При ``cli.system_scope_enabled`` и совпадении cap с ``system_scope_cap_names``
— system manager: ``systemd-run --wait --pipe --uid=0`` (Polkit на хосте).
Иначе — user scope: ``systemd-run --user --scope --quiet``.
"""
import subprocess
from email.message import EmailMessage

from threlium.cli_fsm import parse_cli_intent_payload, resolve_cli_exec_argv
from threlium.fsm_emit import build_fsm_step_to_stage
from threlium.logutil import logger
from threlium.mime_reform import system_part_text
from threlium.prompts import render_prompt
from threlium.settings import ThreliumSettings
from threlium.types import (
    FsmStage,
    MailHeaderName,
    PromptPath,
    ThreliumCapabilitiesBudgetLine,
)

log = logger.bind(stage="cli_exec")


def _peek_cap_top(
    line: ThreliumCapabilitiesBudgetLine | None,
) -> str | None:
    """Вершина стека Capabilities без POP (peek, не мутация).

    POP делает ``egress_router`` при возврате из субагента;
    ``cli_exec`` только читает для выбора ресурсного профиля.
    """
    if line is None or not line.value.strip():
        return None
    parts = line.value.strip().split()
    return parts[-1] if parts else None


def _system_scope_cap_names(settings: ThreliumSettings) -> set[str]:
    raw = settings.cli.system_scope_cap_names
    return {x.strip() for x in raw.split(",") if x.strip()}


def _use_system_scope(cap_name: str | None, config: ThreliumSettings) -> bool:
    if not config.cli.system_scope_enabled:
        return False
    if not cap_name:
        return False
    return cap_name in _system_scope_cap_names(config)


def _build_scope_cmd(
    exec_argv: list[str],
    config: ThreliumSettings,
    *,
    system_scope: bool,
) -> list[str]:
    props = [
        f"--property=MemoryMax={config.cli.exec_memory_max}",
        f"--property=CPUQuota={config.cli.exec_cpu_quota}",
        f"--property=TasksMax={config.cli.exec_tasks_max}",
    ]
    if system_scope:
        return [
            "systemd-run",
            "--wait",
            "--pipe",
            "--uid=0",
            *props,
            "--",
            *exec_argv,
        ]
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        *props,
        "--",
        *exec_argv,
    ]


def main(
    msg: EmailMessage, stage: FsmStage, *, config: ThreliumSettings
) -> EmailMessage | None:
    prior = system_part_text(msg).strip()
    cli = parse_cli_intent_payload(prior)

    if not cli:
        log.warning("no_parseable_payload")
        body = render_prompt(PromptPath.CLI_EXEC_OBSERVATION, cmd_line="", prior=prior)
        # observation → <history> (результат tool в долгую память, origin=cli_exec) +
        # <system> (payload, который ingress прочитает как продолжение хода).
        return build_fsm_step_to_stage(
            msg, to_addr=FsmStage.INGRESS, from_stage=stage,
            history=body, system=body, settings=config,
        )

    # Peek capability profile from X-Threlium-Capabilities stack top
    cap_line = ThreliumCapabilitiesBudgetLine.parse(
        msg.get(MailHeaderName.CAPABILITIES.value)
    )
    cap_name = _peek_cap_top(cap_line) or "default"
    system_scope = _use_system_scope(cap_name, config)

    exec_argv = resolve_cli_exec_argv(cli.argv)
    cmd_line = " ".join(exec_argv)
    log.info(
        "executing",
        cap=cap_name,
        cmd_line=cmd_line,
        system_scope=system_scope,
    )

    scope_cmd = _build_scope_cmd(exec_argv, config, system_scope=system_scope)

    try:
        result = subprocess.run(
            scope_cmd,
            capture_output=True,
            timeout=config.cli.exec_timeout,
            text=True,
            cwd=cli.cwd or None,
        )
        observation = (
            f"exit_code={result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    except subprocess.TimeoutExpired:
        observation = f"TIMEOUT after {config.cli.exec_timeout}s"
        log.error("timeout", timeout_seconds=config.cli.exec_timeout)
    except Exception as e:
        observation = f"exec error: {e}"
        log.error("exec_error", error=str(e))

    body = render_prompt(
        PromptPath.CLI_EXEC_OBSERVATION,
        cmd_line=cmd_line,
        prior=observation,
    )
    # observation (cmd_line + stdout/stderr/exit) → <history> (origin=cli_exec) + <system>.
    # cmd_line уже встроен в рендер, отдельный request_echo не нужен (был бы дублем).
    return build_fsm_step_to_stage(
        msg, to_addr=FsmStage.INGRESS, from_stage=stage,
        history=body, system=body, settings=config,
    )
