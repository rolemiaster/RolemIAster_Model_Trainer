import sys
import os
import json
import logging

logging.basicConfig(level=logging.INFO)

# Append project root to path
sys.path.insert(0, os.path.abspath('.'))

from src.core.test_bench_engine import TestBenchEngine

class MockModelAdapter:
    def generate(self, messages, max_tokens, temperature):
        return "Mock response from the AI."

    def close(self):
        pass

class MockGUI:
    def log(self, *args, **kwargs):
        pass
def run():
    print("Initializing TestBenchEngine...")
    engine = TestBenchEngine(logger=print)
    
    # Patch the _build_adapter method to return our mock
    engine._build_adapter = lambda model_ref, n_ctx: MockModelAdapter()
    
    config = {
        "testbench_target_language": "Español",
        "model_ref": "mock_model_001",
        "n_ctx": 4096,
        "max_tokens": 100,
        "prompt_mode": "isolated"
    }
    
    print("Running suite...")
    results = engine.run_suite(config=config)
    
    print("Suite completed. Saving results.")
    with open("test_results_mock.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    run()
