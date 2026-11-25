"""Demo payloads returned when the Python AI service is unavailable."""

MOCK_NOTE = "Python AI service unavailable — showing demo data."


def user_stories(requirement):
    return [
        {
            "id": "US-001",
            "title": "User Authentication",
            "asA": "registered user",
            "iWant": "to log in securely",
            "soThat": "I can access my personalized dashboard",
            "priority": "High",
            "storyPoints": 5,
            "acceptanceCriteria": [
                "Given valid credentials, When I submit login, Then I am redirected to dashboard",
                "Given invalid password, When I submit, Then I see an error message",
                "Given locked account, When I try to login, Then I see account locked message",
            ],
            "tags": ["authentication", "security"],
        },
        {
            "id": "US-002",
            "title": f"Core Feature: {requirement[:40]}...",
            "asA": "product user",
            "iWant": "to use the main application feature",
            "soThat": "I can achieve my primary goal",
            "priority": "High",
            "storyPoints": 8,
            "acceptanceCriteria": [
                "Given I am logged in, When I navigate to the feature, Then it loads within 2 seconds",
                "Given valid input, When I submit, Then I receive expected output",
                "Given network failure, When I submit, Then I see a retry option",
            ],
            "tags": ["core", "feature"],
        },
        {
            "id": "US-003",
            "title": "Report Generation",
            "asA": "team lead",
            "iWant": "to download test reports as PDF",
            "soThat": "I can share results with stakeholders",
            "priority": "Medium",
            "storyPoints": 3,
            "acceptanceCriteria": [
                "Given a completed test run, When I click download, Then PDF is generated",
                "Given no test runs, When I visit reports, Then I see empty state message",
            ],
            "tags": ["reports", "export"],
        },
    ]


def _field(name, kind, placeholder):
    return {
        "name": name, "type": kind, "id": name, "selector": f"#{name}", "required": True,
        "placeholder": placeholder, "class": "", "maxlength": "", "minlength": "",
        "pattern": "", "value": "", "aria_label": "",
    }


def exploration(url):
    return {
        "start_url": url,
        "pages": [
            {
                "url": url,
                "title": "Home Page",
                "forms": [
                    {
                        "name": "login-form", "action": "/login", "method": "POST", "id": "login-form", "class": "",
                        "fields": [
                            _field("username", "text", "Enter username"),
                            _field("password", "password", "Enter password"),
                        ],
                        "buttons": [
                            {"text": "Login", "type": "submit", "id": "submit-btn", "selector": "#submit-btn", "class": "btn-primary"},
                        ],
                    },
                ],
                "standalone_inputs": [
                    {"tag": "input", "type": "search", "name": "q", "id": "search", "selector": "#search", "text": "", "placeholder": "Search...", "class": "search-input", "aria_label": "Search", "required": False},
                    {"tag": "button", "type": "button", "name": "", "id": "toggle-nav", "selector": "#toggle-nav", "text": "Menu", "placeholder": "", "class": "nav-toggle", "aria_label": "Toggle Navigation", "required": False},
                ],
                "links": [
                    {"text": "About", "href": f"{url}/about"},
                    {"text": "Contact", "href": f"{url}/contact"},
                    {"text": "Pricing", "href": f"{url}/pricing"},
                ],
            },
            {
                "url": f"{url}/about",
                "title": "About Page",
                "forms": [],
                "standalone_inputs": [],
                "links": [{"text": "Home", "href": url}, {"text": "Contact", "href": f"{url}/contact"}],
            },
        ],
        "mock": True,
        "mockNote": MOCK_NOTE,
    }


def combined(requirement, url):
    stories = user_stories(requirement)
    test_cases = []
    for i, story in enumerate(stories):
        test_cases.append({
            "id": f"TC-00{i * 2 + 1}", "storyId": story["id"], "title": f"Verify: {story['title']}",
            "type": "Positive", "priority": story["priority"],
            "steps": [
                {"step": 1, "action": "Navigate to relevant page", "selector": "body"},
                {"step": 2, "action": "Perform primary action", "selector": ".main-action"},
            ],
            "expectedResult": story["acceptanceCriteria"][0], "mapped": True,
        })
        test_cases.append({
            "id": f"TC-00{i * 2 + 2}", "storyId": story["id"], "title": f"Negative: {story['title']}",
            "type": "Negative", "priority": "Medium",
            "steps": [
                {"step": 1, "action": "Navigate to relevant page", "selector": "body"},
                {"step": 2, "action": "Submit invalid data", "selector": ".form-submit"},
            ],
            "expectedResult": "Error message displayed", "mapped": True,
        })
    total = len(test_cases)
    return {
        "story_data": stories,
        "page_data": {"url": url, "pages": [], "mock": True},
        "test_cases": test_cases,
        "summary": {
            "total": total, "mapped": total, "unmapped": 0,
            "by_type": {"Positive": len(stories), "Negative": len(stories), "Boundary": 0, "Edge Case": 0},
        },
        "mock": True,
        "mockNote": MOCK_NOTE,
    }


def extraction(url, query):
    return {
        "start_url": url,
        "query": query,
        "pages": [],
        "aggregated": {
            "pages_crawled": 0,
            "metadata": [{"url": url, "title": "Demo Page", "description": "Mock extraction — Python service unavailable."}],
            "text": {"headings": {"h1": ["Demo Heading"]}, "paragraphs": ["This is demo content because the extraction service is currently offline."], "list_items": []},
            "images": [],
            "links": {"internal": [], "external": []},
            "contact_info": {"emails": ["demo@example.com"], "phones": ["+1-555-0123"], "addresses": ["123 Demo St, Example City, EX 12345"]},
            "tables": [],
            "structured_data": [],
        },
        "ai_summary": "⚠️ Python AI service is unavailable. Showing placeholder data.",
        "mock": True,
        "mockNote": MOCK_NOTE,
    }


def smart_tests(url, query, user_story):
    return {
        "url": url, "query": query, "user_story": user_story,
        "story_breakdown": {"actor": "User", "action": "interact with the website", "goal": "complete the desired task"},
        "components": ["Page Load"],
        "test_suites": [
            {
                "component": "Page Load",
                "test_cases": [
                    {"tc_id": "TC_01", "component": "Page Load", "test_type": "Functional", "test_scenario": "Page loads successfully",
                     "test_steps": [f"Navigate to {url}", "Verify page loads"], "expected_result": "Page loads within 3 seconds",
                     "priority": "Critical", "page_url": url,
                     "automation_steps": [f"Open browser and navigate to '{url}'.", "Assert that the page/response reflects: 'page loaded'."], "mapped": True},
                    {"tc_id": "TC_02", "component": "Page Load", "test_type": "Negative", "test_scenario": "Invalid URL returns error",
                     "test_steps": [f"Navigate to {url}/nonexistent", "Verify error page"], "expected_result": "404 page displayed",
                     "priority": "Medium", "page_url": f"{url}/nonexistent",
                     "automation_steps": [f"Open browser and navigate to '{url}/nonexistent'.", "Assert that the page/response reflects: '404 or error page'."], "mapped": True},
                ],
            },
        ],
        "summary": {"total": 2, "components": 1, "by_type": {"Functional": 1, "Negative": 1}, "by_priority": {"Critical": 1, "Medium": 1}},
        "ai_generated": False, "pages_crawled": 0,
        "mock": True, "mockNote": MOCK_NOTE,
    }
