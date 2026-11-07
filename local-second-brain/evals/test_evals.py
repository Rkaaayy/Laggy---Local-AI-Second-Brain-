import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from importlib import import_module

auth_mod = import_module("04_auth_rbac")
router_mod = import_module("06_router_cache")
main_mod = import_module("main")

GOLDEN_DATASET_PATH = BASE_DIR / "evals" / "golden_personal_qa.json"


def run_all_evaluations():
    """Runs automated regression evals across RAG, RBAC, Guardrails, and Caching."""
    # Clear Redis cache before running evals so every test is fresh
    router_mod.redis_client.delete(
        f"{router_mod.CACHE_KEY_PREFIX}standard",
        f"{router_mod.CACHE_KEY_PREFIX}vault_admin",
    )

    cases = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    passed = 0

    print("=================================================================")
    print("🧪 RUNNING STEP 7: AUTOMATED CI/CD EVALUATION SUITE")
    print("=================================================================")

    for case in cases:
        if case["role"] == "vault_admin":
            token = auth_mod.create_session_token(
                "Hrishikesh", totp_code=auth_mod.get_current_totp_code()
            )
        else:
            token = auth_mod.create_session_token("Hrishikesh")

        result = main_mod.process_aegis_request(case["query"], token)
        ans_lower = result["response"].lower()

        # Check expected keywords
        expected = case.get("expected_keywords", [])
        expected_ok = all(kw.lower() in ans_lower for kw in expected)

        # Check forbidden keywords (ensures secrets never leak to standard role!)
        forbidden = case.get("forbidden_keywords", [])
        forbidden_ok = all(kw.lower() not in ans_lower for kw in forbidden)

        if expected_ok and forbidden_ok:
            passed += 1
            print(f"[✅ PASS] {case['id']} ({result['source']} | {result['model_used']} | {result['latency_sec']}s)")
        else:
            print(f"[❌ FAIL] {case['id']}")
            print(f"   Response was: {result['response']}")

    print("=================================================================")
    print(f"EVALUATION SCORECARD: {passed}/{len(cases)} TESTS PASSED ({passed/len(cases)*100:.0f}%)")
    print("=================================================================")
    assert passed == len(cases), "One or more evaluation tests failed!"


def test_aegis_suite():
    run_all_evaluations()


if __name__ == "__main__":
    run_all_evaluations()