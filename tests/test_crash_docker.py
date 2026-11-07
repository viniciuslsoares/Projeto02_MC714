"""Testa os comandos do Docker usados pelo injetor em crash/recover (sem rodar Docker de verdade)."""
import subprocess

from experiments.fault_injector import FaultInjector


def _capturar_chamadas(monkeypatch, codigo=0):
    # Troca subprocess.run por uma função que só anota o comando recebido
    chamadas = []

    def run_falso(cmd, **kwargs):
        chamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, codigo)

    monkeypatch.setattr("experiments.fault_injector.subprocess.run", run_falso)
    return chamadas


def test_crash_usa_docker_kill_e_recover_usa_docker_start(monkeypatch):
    chamadas = _capturar_chamadas(monkeypatch)
    injetor = FaultInjector(modo_docker=True)

    injetor.executar_evento({"time_s": 0.0, "action": "crash", "node": 2})
    injetor.executar_evento({"time_s": 0.0, "action": "recover", "node": 2})

    # crash precisa ser SIGKILL (docker kill), não SIGTERM (docker stop)
    assert chamadas == [
        ["docker", "kill", "mc714-node2"],
        ["docker", "start", "mc714-node2"],
    ]


def test_crash_avisa_quando_docker_falha(monkeypatch, capsys):
    _capturar_chamadas(monkeypatch, codigo=1)
    injetor = FaultInjector(modo_docker=True)

    injetor.executar_evento({"time_s": 0.0, "action": "crash", "node": 2})

    assert "[Aviso]" in capsys.readouterr().out
