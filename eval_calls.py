"""Check deterministic call-center routing cases before a voice release."""
import json

from callcenter import decide

CASES = "eval_calls.jsonl"


def outcome(decision):
    return decision.outcome.value if decision.outcome else "model"


def main():
    failures = []
    total = 0
    for line in open(CASES, encoding="utf-8"):
        case = json.loads(line)
        history, last_answer = [], ""
        actual = []
        for question in case["turns"]:
            decision = decide(question, last_answer, history)
            actual.append(outcome(decision))
            answer = decision.message or "[përgjigje e mbështetur nga burimi]"
            history.extend((
                {"role": "user", "content": decision.question or "[turn i mbrojtur]"},
                {"role": "assistant", "content": answer},
            ))
            last_answer = answer
        total += 1
        if actual != case["expected"]:
            failures.append((case["name"], case["expected"], actual))

    if failures:
        for name, expected, actual in failures:
            print(f"FAIL {name}: expected {expected}, got {actual}")
        raise SystemExit(1)
    print(f"call-policy eval passed: {total}/{total}")


if __name__ == "__main__":
    main()
