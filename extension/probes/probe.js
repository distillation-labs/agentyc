const result = document.querySelector("#result");
const tabs = await chrome.tabs.query({active: true, currentWindow: true});
const tab = tabs[0];
result.textContent = tab ? `Target: ${tab.title ?? "untitled"}` : "No active tab.";
document.querySelector("#run").addEventListener("click", async () => {
  result.textContent = "Running…";
  try {
    const response = await chrome.runtime.sendMessage({type: "run-probe", tab_id: tab?.id});
    result.textContent = JSON.stringify(response, null, 2);
  } catch (error) {
    result.textContent = `Failed closed: ${String(error)}`;
  }
});
