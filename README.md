# StoryAnalystAI

**StoryAnalystAI** is a robust, AI-powered QA platform that automatically generates, executes, and analyzes test cases starting from a simple user story. The system bridges a modern React frontend, a Node.js API Gateway, and a pure Python AI microservice that reliably crawls pages and executes headless Selenium tests dynamically.

## 🏗 System Architecture

The application is split into three primary components:

1. **Python AI Microservice** (`/app.py`)
   Runs the Heavy-Lifting Agents: LLM Orchestrator, Website Explorer, Test Case Generator, Test Executor (Parallel-Enabled), and Report Agent.
2. **Node.js Gateway API** (`/backend`)
   Handles authentication, authorization, session storage, Firebase integration, and proxies complex AI requests to the Python Microservice. 
3. **React Frontend UI** (`/frontend`)
   Vite-optimized React single-page application handling the user dashboards, interactive reports, and execution controls.

---

## ⚙️ Prerequisites

Before you begin, ensure you have the following installed on your machine:

- **Node.js**: v18 or later
- **Python**: v3.10 or later
- **Git**
- **Google Chrome** (Required for the Selenium automated test executions)

---

## 🚀 How to Run the Application Locally

You will need to open **three separate terminal instances** to run the complete stack concurrently.

### 1. Start the Python AI Microservice (Terminal 1)

This service manages all AI prompts, web crawling, and test execution.

```bash
# 1. Open a terminal at the project root folder
cd "StoryAnalystAI"

# 2. (Recommended) Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\activate   # On Windows
# source venv/bin/activate  # On macOS/Linux

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Start the Flask microservice
python app.py
```
> **Note:** The Python service typically starts on `http://localhost:10000`.

### 2. Start the Node.js Backend API (Terminal 2)

This service connects the database and secures the connection to the Python engine.

```bash
# 1. Navigate to the backend directory
cd "StoryAnalystAI/backend"

# 2. Install Node dependencies
npm install

# 3. Ensure your .env variables are set (you can copy .env.example to .env)
# The backend must be able to reach the Python service via PYTHON_AI_URL

# 4. Start the Node API Server
npm start
# OR use "npm run dev" if you have nodemon configured
```
> **Note:** The Node.js API server typically listens on `http://localhost:5000`.

### 3. Start the React Frontend Dashboard (Terminal 3)

This provides you with the modern UI dashboard to interact with the platform.

```bash
# 1. Navigate to the frontend directory
cd "StoryAnalystAI/frontend"

# 2. Install Frontend dependencies
npm install

# 3. Build and Start the Vite development server
npm run dev
```
> **Note:** The frontend application will be hosted on `http://localhost:5173`. Open this URL in your browser to access the StoryAnalystAI dashboard!

---

## 📝 Usage Workflow

1. **User Stories:** Go to the User Story tab and define out testing conditions.
2. **Website Exploration:** Feed an application URL into the Website Explorer. StoryAnalystAI will safely scout out elements (buttons, forms, inputs) and build an internal map.
3. **Combined Output:** Generate a consolidated map where user stories are rigorously turned into test cases configured explicitly for your explored DOM structure. 
4. **Execution:** Hop into the visual Test Execution Engine. Select your parallel worker count and execute directly inside headless Chrome browsers.
5. **Reports:** Generate polished HTML/PDF test summaries or download execution results inside the Reports tab.

*(To run tests in a fully headless CLI setup without the UI, you can execute `python build_report.py` at the root).*