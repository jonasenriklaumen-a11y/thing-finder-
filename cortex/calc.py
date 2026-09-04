"""Sicherer Rechner fuer den Agenten.

Kleine lokale Modelle verrechnen sich zuverlaessig bei Preis-pro-Einheit,
Rabatten und Umrechnungen -- und cortexs Kernversprechen ist, nicht zu
raten. Gerechnet wird deshalb hier, ueber einen AST mit strikter
Positivliste. Kein `eval`, keine Namen, keine Funktionsaufrufe.
"""

from __future__ import annotations

import ast
import math
import operator
import re

#: Obergrenze fuer Exponenten, damit `9**9**9` nicht die CPU frisst.
MAX_POWER = 1000
MAX_EXPRESSION_LENGTH = 300

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: None,  # eigener Zweig mit Grenze
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class CalcError(ValueError):
    """Der Ausdruck laesst sich nicht sicher auswerten."""


def normalize_numbers(expression: str) -> str:
    """Macht deutsche Zahlschreibweisen rechenbar.

    `1.099,99` -> `1099.99`; ein einzelnes `12,5` -> `12.5`. Punkte werden
    nur als Tausendertrenner entfernt, wenn auch ein Komma vorkommt --
    `12.5` allein bleibt englisch gelesen.
    """

    def _one(match: re.Match[str]) -> str:
        token = match.group(0)
        if "," in token:
            return token.replace(".", "").replace(",", ".")
        return token

    return re.sub(r"\d[\d.]*,\d+|\d[\d.,]*", _one, expression)


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise CalcError(f"Nur Zahlen sind erlaubt, nicht {node.value!r}.")
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > 12 or abs(left) > MAX_POWER:
                raise CalcError("Exponent zu gross.")
            return left**right
        try:
            return _BINARY[type(node.op)](left, right)
        except ZeroDivisionError as exc:
            raise CalcError("Division durch null.") from exc
    raise CalcError(f"'{type(node).__name__}' ist in Ausdruecken nicht erlaubt.")


def calculate(expression: str) -> float:
    """Wertet einen arithmetischen Ausdruck aus.

    Erlaubt sind Zahlen, + - * / // % ** und Klammern -- sonst nichts.

    Raises:
        CalcError: Bei allem, was keine reine Arithmetik ist.
    """
    expression = (expression or "").strip()
    if not expression:
        raise CalcError("Leerer Ausdruck.")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalcError("Ausdruck zu lang.")
    expression = normalize_numbers(expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"Kein gueltiger Ausdruck: {exc.msg}.") from exc
    result = _evaluate(tree)
    if isinstance(result, float) and (math.isinf(result) or math.isnan(result)):
        raise CalcError("Ergebnis ausserhalb des darstellbaren Bereichs.")
    return result


def calculate_pretty(expression: str) -> str:
    """Ergebnis als String, ganzzahlig ohne Nachkommastellen."""
    value = calculate(expression)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{round(value, 6):g}"
    return str(value)
