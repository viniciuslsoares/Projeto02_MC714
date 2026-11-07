"""Testes da semente por nó: SEED é a do experimento e cada nó deriva a sua."""
from src import config as cfg


def test_mesma_seed_gera_sementes_diferentes_por_no():
    c1 = cfg.from_env({"NODE_ID": "1", "SEED": "1001"})
    c2 = cfg.from_env({"NODE_ID": "2", "SEED": "1001"})
    assert c1.seed != c2.seed
    assert c1.seed == 1001 * 1000 + 1 and c2.seed == 1001 * 1000 + 2


def test_derivacao_e_deterministica():
    env = {"NODE_ID": "3", "SEED": "42"}
    assert cfg.from_env(env).seed == cfg.from_env(env).seed == 42 * 1000 + 3


def test_sem_seed_continua_funcionando():
    c1 = cfg.from_env({"NODE_ID": "1"})
    c2 = cfg.from_env({"NODE_ID": "2"})
    assert c1.seed == 1 and c2.seed == 2    # SEED padrão 0 => semente = NODE_ID
