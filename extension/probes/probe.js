const result = document.querySelector("#result");
const runButton = document.querySelector("#run");
result.textContent =
  "Canonical local fixture only. Nothing runs automatically.";

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  result.textContent = "Running…";
  try {
    const response = await chrome.runtime.sendMessage({
      type: "run-probe",
      request_id: crypto.randomUUID(),
    });
    const { request_id: _requestId, ...displayResponse } = response ?? {};
    result.textContent = JSON.stringify(displayResponse, null, 2);
  } catch (_) {
    result.textContent = "Failed closed: probe request failed.";
  } finally {
    runButton.disabled = false;
  }
});
