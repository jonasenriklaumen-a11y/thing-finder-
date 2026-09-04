"""Tests fuer den sicheren Rechner."""

from __future__ import annotations

import pytest

from cortex.calc import CalcError, calculate, calculate_pretty, normalize_numbers


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+3*4", 14),
        ("(1099 + 1149 + 1049) / 3", pytest.approx(1099.0)),
        ("2**10", 1024),
        ("10 % 3", 1),
        ("7 // 2", 3),
        ("-5 + 3", -2),
        ("1.5 * 2", 3.0),
    ],
)
def test_arithmetic(expression: str, expected: object) -> None:
    assert calculate(expression) == expected


def test_german_number_formats() -> None:
    """Preise stehen auf deutschen Seiten als 1.099,99."""
    assert calculate("1.099,99 + 0,01") == pytest.approx(1100.0)
    assert calculate("12,5 * 2") == pytest.approx(25.0)
    # Englische Schreibweise bleibt englisch.
    assert calculate("12.5 * 2") == pytest.approx(25.0)


def test_normalize_leaves_operators_alone() -> None:
    assert normalize_numbers("(1.099,00 + 5) / 2") == "(1099.00 + 5) / 2"


@pytest.mark.parametrize(
    "evil",
    [
        "__import__('os').system('rm -rf /')",
        "open('/etc/passwd')",
        "x + 1",
        "[1,2][0]",
        "'a' * 9999",
        "lambda: 1",
        "1 if True else 2",
    ],
)
def test_everything_but_arithmetic_is_rejected(evil: str) -> None:
    with pytest.raises(CalcError):
        calculate(evil)


def test_resource_limits() -> None:
    with pytest.raises(CalcError, match="Exponent"):
        calculate("9**9**9")
    with pytest.raises(CalcError, match="zu lang"):
        calculate("1+" * 200 + "1")


def test_division_by_zero_is_a_clean_error() -> None:
    with pytest.raises(CalcError, match="null"):
        calculate("1/0")


def test_pretty_output() -> None:
    assert calculate_pretty("4/2") == "2"
    assert calculate_pretty("10/4") == "2.5"
    assert calculate_pretty("1/3") == "0.333333"
