const axios = require("axios");

async function run() {
  try {
    const payload = {
        test_cases: [
            {
                tc_id: "TC-01",
                page_url: "https://example.com",
                automation_steps: ["assert text 'Example Domain'"]
            }
        ],
        workers: 1
    };
    
    // Simulate frontend call to Node server (we'd need a token, so let's skip the token and just call Python directly,
    // wait I need to test Node server logic...)
    
    // Actually, let's just make a script that tests the Python API exactly as Node does.
    const internalSecret = "dev_secret_change_me";
    const res = await axios.post("http://localhost:10000/execute", {
        test_cases: payload.test_cases,
        headless: true,
        workers: 1
    }, {
        headers: { "X-Internal-Secret": internalSecret }
    });
    console.log("Python response:", JSON.stringify(res.data, null, 2));

  } catch (err) {
    console.error("Error:", err.response?.data || err.message);
  }
}
run();
