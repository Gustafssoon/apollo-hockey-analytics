from pathlib import Path

import pytest

from apollo import cli_v10
from apollo.draft import DraftConfigError, draft_picks, load_draft_config, snake_overall_pick


VALID_CONFIG = """\
league:
  name: Apollo Test League
  teams: 12

draft:
  type: snake
  my_slot: 8
  rounds: 16

roster:
  C: 2
  LW: 2
  RW: 2
  D: 4
  G: 2
  BN: 4

scoring:
  skaters:
    G: 6
    A: 4
    PPP: 2
    SOG: 0.5
    HIT: 0.5
    BLK: 0.5
  goalies:
    W: 4
    SV: 0.4
    GA: -1.5
    SO: 3
"""


def _write_config(tmp_path: Path, text: str = VALID_CONFIG) -> Path:
    path = tmp_path / "draft.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _replace(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


def test_valid_config_loads(tmp_path):
    config = load_draft_config(_write_config(tmp_path))

    assert config.league.name == "Apollo Test League"
    assert config.league.teams == 12
    assert config.draft.draft_type == "snake"
    assert config.draft.my_slot == 8
    assert config.draft.rounds == 16
    assert [(slot.name, slot.count) for slot in config.roster] == [
        ("C", 2),
        ("LW", 2),
        ("RW", 2),
        ("D", 4),
        ("G", 2),
        ("BN", 4),
    ]


def test_teams_below_two_is_rejected(tmp_path):
    text = _replace(VALID_CONFIG, "  teams: 12", "  teams: 1")

    with pytest.raises(DraftConfigError, match=r"league\.teams must be >= 2"):
        load_draft_config(_write_config(tmp_path, text))


def test_slot_zero_is_rejected(tmp_path):
    text = _replace(VALID_CONFIG, "  my_slot: 8", "  my_slot: 0")

    with pytest.raises(DraftConfigError, match="draft.my_slot must be between 1 and 12"):
        load_draft_config(_write_config(tmp_path, text))


def test_slot_above_number_of_teams_is_rejected(tmp_path):
    text = _replace(VALID_CONFIG, "  my_slot: 8", "  my_slot: 13")

    with pytest.raises(DraftConfigError, match="draft.my_slot must be between 1 and 12"):
        load_draft_config(_write_config(tmp_path, text))


def test_rounds_below_one_is_rejected(tmp_path):
    text = _replace(VALID_CONFIG, "  rounds: 16", "  rounds: 0")

    with pytest.raises(DraftConfigError, match=r"draft\.rounds must be >= 1"):
        load_draft_config(_write_config(tmp_path, text))


def test_negative_roster_slot_is_rejected(tmp_path):
    text = _replace(VALID_CONFIG, "  RW: 2", "  RW: -1")

    with pytest.raises(DraftConfigError, match=r"roster\.RW must be >= 0"):
        load_draft_config(_write_config(tmp_path, text))


def test_unsupported_draft_type_is_rejected(tmp_path):
    text = _replace(VALID_CONFIG, "  type: snake", "  type: auction")

    with pytest.raises(DraftConfigError, match="currently supports only 'snake'"):
        load_draft_config(_write_config(tmp_path, text))


def test_negative_goalie_scoring_is_accepted(tmp_path):
    config = load_draft_config(_write_config(tmp_path))
    goalie_scoring = {category.stat: category.points for category in config.scoring.goalies}

    assert goalie_scoring["GA"] == -1.5


def test_missing_required_configuration_fails_cleanly(tmp_path):
    text = VALID_CONFIG.replace("league:\n  name: Apollo Test League\n  teams: 12\n\n", "", 1)

    with pytest.raises(DraftConfigError, match="Missing required section: league"):
        load_draft_config(_write_config(tmp_path, text))


def test_malformed_yaml_fails_cleanly(tmp_path):
    path = _write_config(tmp_path, "league: [broken\n")

    with pytest.raises(DraftConfigError, match="Invalid YAML"):
        load_draft_config(path)


def test_snake_picks_for_slot_eight_in_twelve_team_league():
    picks = [snake_overall_pick(12, 8, round_number) for round_number in range(1, 7)]

    assert picks == [8, 17, 32, 41, 56, 65]


def test_snake_picks_for_edge_slots():
    assert [snake_overall_pick(12, 1, round_number) for round_number in range(1, 5)] == [
        1,
        24,
        25,
        48,
    ]
    assert [snake_overall_pick(12, 12, round_number) for round_number in range(1, 5)] == [
        12,
        13,
        36,
        37,
    ]


def test_configured_number_of_rounds_is_respected(tmp_path):
    text = _replace(VALID_CONFIG, "  rounds: 16", "  rounds: 3")
    config = load_draft_config(_write_config(tmp_path, text))

    assert [(pick.round_number, pick.overall_pick) for pick in draft_picks(config)] == [
        (1, 8),
        (2, 17),
        (3, 32),
    ]


def test_draft_config_show_cli(tmp_path, capsys):
    path = _write_config(tmp_path)

    cli_v10.main(["draft", "config", "show", "--config", str(path)])

    output = capsys.readouterr().out
    assert "APOLLO DRAFT CONFIG" in output
    assert "Apollo Test League" in output
    assert "Teams:       12" in output
    assert "Your slot:   #8" in output
    assert "R1   #8" in output
    assert "R2   #17" in output
    assert "GA       -1.5" in output


def test_draft_config_show_cli_invalid_config_exits_nonzero(tmp_path):
    text = _replace(VALID_CONFIG, "  my_slot: 8", "  my_slot: 99")
    path = _write_config(tmp_path, text)

    with pytest.raises(SystemExit, match="Draft config error: draft.my_slot"):
        cli_v10.main(["draft", "config", "show", "--config", str(path)])
