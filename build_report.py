import sys
import json
import traceback
from pathlib import Path

# Add the project root to sys.path
sys.path.append(str(Path(__file__).parent))

from agents.combined_generator import CombinedGeneratorAgent
from agents.test_executor import TestExecutorAgent
from agents.website_explorer import WebsiteExplorerAgent
from agents.report_agent import ReportAgent

def main():
    print("🚀 Starting StoryAnalystAI Full Pipeline...")
    
    # 1. Define story
    story_data = {
        "feature": "Sign Up",
        "user_role": "User",
        "conditions": [
            "Valid Sign Up",
            "Sign up with missing name",
            "Sign up with invalid email"
        ]
    }
    
    # 2. Explore URL
    url = "http://127.0.0.1:3000/test-web/index.html?serverWindowId=8d1b882a-39e2-480a-9b23-b7ef332d43e2"
    print(f"🔍 Exploring Website: {url}")
    explorer = WebsiteExplorerAgent()
    try:
        page_data = explorer.explore(url, depth=1)
    except Exception as e:
        print(f"⚠️ Error exploring website, using empty page data. {e}")
        page_data = {"url": url, "pages": []}

    # 3. Generate Test Cases
    print("🧠 Generating Combined Test Cases...")
    combiner = CombinedGeneratorAgent()
    tcs = combiner.generate(story_data, page_data)
    print(f"✅ Generated {len(tcs)} test cases.")
    
    # 4. Execute Test Cases
    print("🔬 Executing Test Cases...")
    executor = TestExecutorAgent()
    results_raw = executor.execute_all(tcs, headless=True, workers=1)
    
    passed = sum(1 for r in results_raw if r["status"] == "Pass")
    failed = sum(1 for r in results_raw if r["status"] == "Fail")
    errored = sum(1 for r in results_raw if r["status"] == "Error")
    
    exec_data = {
        "results": results_raw,
        "summary": {
            "total": len(results_raw),
            "passed": passed,
            "failed": failed,
            "errored": errored
        }
    }
    print(f"✅ Execution Complete: {passed} Pass, {failed} Fail, {errored} Error")
    
    # Dump raw JSON just in case
    with open("latest_execution_results.json", "w", encoding="utf-8") as f:
        json.dump(exec_data, f, indent=2)
    
    # 5. Generate Report
    print("📊 Generating HTML Report using ReportAgent...")
    reporter = ReportAgent()
    html_report = reporter.generate(exec_data)
    
    report_path = Path("test_execution_report.html")
    report_path.write_text(html_report, encoding="utf-8")
    
    print(f"🎉 Success! The report has been built properly at: {report_path.resolve()}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Pipeline failed: {e}")
        traceback.print_exc()
