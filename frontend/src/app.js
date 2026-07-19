// API Endpoint configurations
const BACKEND_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_URLS = {
  auth: `${BACKEND_URL}/auth`,
  ingest: `${BACKEND_URL}/ingest`,
  analytics: `${BACKEND_URL}/analytics`,
  report: `${BACKEND_URL}/report`
};

// Global App State
let appState = {
  token: localStorage.getItem("token") || null,
  userEmail: localStorage.getItem("userEmail") || null,
  companyId: localStorage.getItem("companyId") || null,
  activePanel: "analytics",
  chartInstance: null,
  pollIntervalId: null,
  selectedFile: null
};

// --- Initial Startup ---
document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  initView();
});

function initView() {
  if (appState.token) {
    showDashboardView();
  } else {
    showAuthView();
  }
}

// --- View Routers ---
function showAuthView() {
  document.getElementById("auth-view").classList.remove("hidden");
  document.getElementById("dashboard-view").classList.add("hidden");
  
  // Reset fields
  document.getElementById("login-form").reset();
  document.getElementById("register-form").reset();
  hideAlert("auth-alert");
}

function showDashboardView() {
  document.getElementById("auth-view").classList.add("hidden");
  document.getElementById("dashboard-view").classList.remove("hidden");
  
  document.getElementById("display-user-email").textContent = appState.userEmail;
  document.getElementById("display-company-id").textContent = appState.companyId;
  
  switchPanel("analytics");
}

function switchPanel(panelName) {
  appState.activePanel = panelName;
  
  // Hide all sections
  document.getElementById("panel-analytics").classList.add("hidden");
  document.getElementById("panel-ingest").classList.add("hidden");
  document.getElementById("panel-reports").classList.add("hidden");
  
  // Remove active sidebar state
  document.getElementById("nav-analytics").classList.remove("active");
  document.getElementById("nav-ingest").classList.remove("active");
  document.getElementById("nav-reports").classList.remove("active");

  // Show active panel
  document.getElementById(`panel-${panelName}`).classList.remove("hidden");
  document.getElementById(`nav-${panelName}`).classList.add("active");
  
  // Set View Title
  const titles = {
    analytics: "Sales Dashboard",
    ingest: "Data Ingestion & Imports",
    reports: "Reports & Exporters"
  };
  document.getElementById("view-title").textContent = titles[panelName];

  // Stop polling when leaving ingestion panel
  if (panelName !== "ingest" && appState.pollIntervalId) {
    clearInterval(appState.pollIntervalId);
    appState.pollIntervalId = null;
  }

  // Panel Specific Loaders
  if (panelName === "analytics") {
    loadAnalyticsDashboard();
  }
}

// --- API Helpers ---
async function authenticatedFetch(service, endpoint, options = {}) {
  if (!appState.token) {
    handleSessionExpired();
    throw new Error("No authorization token present.");
  }

  const url = `${API_URLS[service]}${endpoint}`;
  
  // Add authentication headers
  options.headers = {
    ...options.headers,
    "Authorization": `Bearer ${appState.token}`
  };

  try {
    const response = await fetch(url, options);
    
    // Auth failures
    if (response.status === 401) {
      handleSessionExpired();
      throw new Error("Session expired. Please sign in again.");
    }
    
    return response;
  } catch (error) {
    console.error(`Fetch error on ${url}:`, error);
    throw error;
  }
}

function handleSessionExpired() {
  localStorage.clear();
  appState.token = null;
  appState.userEmail = null;
  appState.companyId = null;
  if (appState.pollIntervalId) {
    clearInterval(appState.pollIntervalId);
    appState.pollIntervalId = null;
  }
  showAuthView();
}

// --- Authentication Operations ---
async function handleLogin(e) {
  e.preventDefault();
  hideAlert("auth-alert");
  
  const email = document.getElementById("login-email").value;
  const password = document.getElementById("login-password").value;
  
  try {
    // URLSearchParams for Form UrlEncoded content type matching OAuth2 specifications
    const params = new URLSearchParams();
    params.append("username", email);
    params.append("password", password);

    const response = await fetch(`${API_URLS.auth}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: params
    });

    const data = await response.json();
    
    if (!response.ok) {
      throw new Error(data.detail || "Authentication login failed");
    }

    // Save tokens and user info
    appState.token = data.access_token;
    appState.userEmail = email;
    
    // Parse company ID from access token (contains company_id claims)
    const tokenPayload = parseJwt(data.access_token);
    appState.companyId = tokenPayload.company_id || "10";

    localStorage.setItem("token", appState.token);
    localStorage.setItem("userEmail", appState.userEmail);
    localStorage.setItem("companyId", appState.companyId);

    showDashboardView();
  } catch (err) {
    showAlert("auth-alert", err.message, "error");
  }
}

async function handleRegister(e) {
  e.preventDefault();
  hideAlert("auth-alert");

  const email = document.getElementById("reg-email").value;
  const company_id = parseInt(document.getElementById("reg-company").value);
  const password = document.getElementById("reg-password").value;

  try {
    const response = await fetch(`${API_URLS.auth}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, company_id, password })
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Registration failed");
    }

    showAlert("auth-alert", "Account created successfully! Logging you in...", "success");
    
    // Automatically trigger login
    document.getElementById("login-email").value = email;
    document.getElementById("login-password").value = password;
    setTimeout(() => {
      document.getElementById("tab-login").click();
      document.getElementById("login-form").dispatchEvent(new Event("submit"));
    }, 1200);

  } catch (err) {
    showAlert("auth-alert", err.message, "error");
  }
}

async function handleLogout() {
  try {
    // Notify auth server to blacklist token
    await authenticatedFetch("auth", "/logout", { method: "POST" });
  } catch (e) {
    // Continue cleanup locally anyway
  }
  handleSessionExpired();
}

// --- Ingestion & Uploads Processing ---
function handleFileSelect(e) {
  const files = e.target.files || e.dataTransfer.files;
  if (!files.length) return;

  const file = files[0];
  const name = file.name;
  const ext = name.split(".").pop().toLowerCase();

  if (ext !== "csv" && ext !== "xlsx") {
    showAlert("upload-status", "Unsupported file type. Please upload a .csv or .xlsx sheet.", "error");
    appState.selectedFile = null;
    document.getElementById("btn-upload").disabled = true;
    document.getElementById("file-label").textContent = "Drag & Drop CSV / Excel or click to select";
    return;
  }

  appState.selectedFile = file;
  document.getElementById("file-label").innerHTML = `<i class="fa-solid fa-file-circle-check"></i> Selected: <strong>${name}</strong>`;
  document.getElementById("btn-upload").disabled = false;
  hideAlert("upload-status");
}

async function handleFileUpload() {
  if (!appState.selectedFile) return;

  hideAlert("upload-status");
  const uploadBtn = document.getElementById("btn-upload");
  uploadBtn.disabled = true;
  uploadBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Processing...`;

  const formData = new FormData();
  formData.append("file", appState.selectedFile);

  const fileExt = appState.selectedFile.name.split(".").pop().toLowerCase();
  const endpoint = fileExt === "csv" ? "/upload/csv" : "/upload/excel";

  try {
    const response = await authenticatedFetch("ingest", endpoint, {
      method: "POST",
      body: formData
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "File ingestion failed");
    }

    showAlert("upload-status", `File enqueued! Ingestion job #${data.job_id} scheduled.`, "success");
    
    // Reset file selectors
    appState.selectedFile = null;
    document.getElementById("file-input").value = "";
    document.getElementById("file-label").textContent = "Drag & Drop CSV / Excel or click to select";
    
    // Start tracking job status
    startJobTracking(data.job_id, fileExt.toUpperCase());

  } catch (err) {
    showAlert("upload-status", err.message, "error");
  } finally {
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = `<i class="fa-solid fa-paper-plane"></i> Upload & Process`;
  }
}

async function handleDatabaseImport(e) {
  e.preventDefault();
  hideAlert("db-alert");

  const dbType = document.getElementById("db-type").value;
  const host = document.getElementById("db-host").value;
  const port = parseInt(document.getElementById("db-port").value);
  const username = document.getElementById("db-username").value;
  const password = document.getElementById("db-password").value;
  const database = document.getElementById("db-name").value;
  const tableName = document.getElementById("db-table").value;

  const endpoint = dbType === "postgres" ? "/import/postgres" : "/import/mysql";
  const payload = { host, port, username, password, database, table_name: tableName };

  try {
    const response = await authenticatedFetch("ingest", endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Database connection / import enqueuing failed");
    }

    showAlert("db-alert", `Success! Database Import Job #${data.job_id} enqueued.`, "success");
    document.getElementById("db-connector-form").reset();
    
    // Start tracking job status
    startJobTracking(data.job_id, `DB (${dbType.toUpperCase()})`);

  } catch (err) {
    showAlert("db-alert", err.message, "error");
  }
}

async function testDatabaseConnection() {
  hideAlert("db-alert");

  const dbType = document.getElementById("db-type").value;
  const host = document.getElementById("db-host").value;
  const port = parseInt(document.getElementById("db-port").value);
  const username = document.getElementById("db-username").value;
  const password = document.getElementById("db-password").value;
  const database = document.getElementById("db-name").value;

  const endpoint = dbType === "postgres" ? "/ping/postgres" : "/ping/mysql";
  const payload = { host, port, username, password, database };

  try {
    const response = await authenticatedFetch("ingest", endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await response.json();

    if (!response.ok || !data.connected) {
      throw new Error(data.detail || "Unable to establish connection to database.");
    }

    showAlert("db-alert", "Connection Test Successful! Credentials are correct.", "success");
  } catch (err) {
    showAlert("db-alert", err.message, "error");
  }
}

// --- Ingestion Tracker & Polling ---
function startJobTracking(jobId, jobType) {
  if (appState.pollIntervalId) {
    clearInterval(appState.pollIntervalId);
  }

  // Initialize Tracker Card UI
  document.getElementById("tracker-job-id").textContent = jobId;
  document.getElementById("tracker-job-type").textContent = jobType;
  
  const statusBadge = document.getElementById("tracker-job-status");
  statusBadge.className = "status-badge pending";
  statusBadge.textContent = "pending";
  
  document.getElementById("tracker-job-rows").textContent = "0";
  document.getElementById("tracker-error-container").classList.add("hidden");

  // Poll job status every 2 seconds
  appState.pollIntervalId = setInterval(async () => {
    try {
      const response = await authenticatedFetch("ingest", `/jobs/${jobId}`);
      const job = await response.json();

      if (!response.ok) return;

      statusBadge.textContent = job.status;
      statusBadge.className = `status-badge ${job.status.toLowerCase()}`;
      document.getElementById("tracker-job-rows").textContent = job.rows_processed || "0";

      if (job.status === "completed") {
        clearInterval(appState.pollIntervalId);
        appState.pollIntervalId = null;
        // Refresh dashboard background
        loadAnalyticsDashboard();
      } else if (job.status === "failed") {
        clearInterval(appState.pollIntervalId);
        appState.pollIntervalId = null;
        document.getElementById("tracker-job-error").textContent = job.error_message || "Unknown error";
        document.getElementById("tracker-error-container").classList.remove("hidden");
      }

    } catch (e) {
      clearInterval(appState.pollIntervalId);
      appState.pollIntervalId = null;
    }
  }, 2000);
}

// --- Analytics Loading & Charts ---
async function loadAnalyticsDashboard() {
  try {
    // 1. Fetch dashboard summaries
    const response = await authenticatedFetch("analytics", "/dashboard");
    const kpis = await response.json();

    document.getElementById("kpi-revenue").textContent = formatCurrency(kpis.total_revenue);
    document.getElementById("kpi-profit").textContent = formatCurrency(kpis.total_profit);
    document.getElementById("kpi-margin").textContent = `${kpis.profit_margin_pct.toFixed(2)}%`;
    document.getElementById("kpi-transactions").textContent = kpis.total_transactions;
    document.getElementById("kpi-products").textContent = kpis.unique_products;
    document.getElementById("kpi-customers").textContent = kpis.unique_customers;

    // 2. Fetch monthly trend charts
    const monthlyResponse = await authenticatedFetch("analytics", "/sales/monthly");
    const trends = await monthlyResponse.json();
    renderTrendsChart(trends);

    // 3. Fetch forecasting projections
    const forecastResponse = await authenticatedFetch("analytics", "/forecast?steps=3");
    const forecast = await forecastResponse.json();
    renderForecast(forecast);

    // 4. Fetch Top Products
    const productsResponse = await authenticatedFetch("analytics", "/top-products?limit=5");
    const products = await productsResponse.json();
    renderTopProducts(products);

    // 5. Fetch Top Customers
    const customersResponse = await authenticatedFetch("analytics", "/top-customers?limit=5");
    const customers = await customersResponse.json();
    renderTopCustomers(customers);

  } catch (err) {
    console.error("Failed to load analytics panel summaries:", err);
  }
}

function renderTrendsChart(trends) {
  const ctx = document.getElementById("trendsChart").getContext("2d");
  
  if (appState.chartInstance) {
    appState.chartInstance.destroy();
  }

  const labels = trends.map(t => t.month);
  const revenues = trends.map(t => t.revenue);
  const profits = trends.map(t => t.profit);

  appState.chartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Revenue",
          data: revenues,
          backgroundColor: "rgba(59, 130, 246, 0.4)",
          borderColor: "#3b82f6",
          borderWidth: 2,
          borderRadius: 6
        },
        {
          label: "Profit",
          data: profits,
          type: "line",
          borderColor: "#10b981",
          borderWidth: 3,
          backgroundColor: "transparent",
          tension: 0.3
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: "#e5e7eb", font: { family: "Outfit" } }
        }
      },
      scales: {
        x: {
          ticks: { color: "#9ca3af", font: { family: "Outfit" } },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        },
        y: {
          ticks: { color: "#9ca3af", font: { family: "Outfit" } },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        }
      }
    }
  });
}

function renderForecast(forecast) {
  document.getElementById("forecast-method").textContent = `Method: ${forecast.method.replace("_", " ")}`;
  const container = document.getElementById("forecast-rows");
  container.innerHTML = "";

  if (!forecast.forecasted.length) {
    container.innerHTML = `<div class="forecast-item">No forecast projections available yet.</div>`;
    return;
  }

  forecast.forecasted.forEach(item => {
    const el = document.createElement("div");
    el.className = "forecast-item";
    el.innerHTML = `
      <div class="forecast-date">${formatMonthName(item.month)}</div>
      <div class="forecast-values">
        <div class="forecast-rev">${formatCurrency(item.revenue)}</div>
        <div class="forecast-prof">Profit: ${formatCurrency(item.profit)}</div>
      </div>
    `;
    container.appendChild(el);
  });
}

function renderTopProducts(products) {
  const tbody = document.getElementById("top-products-table");
  tbody.innerHTML = "";
  if (!products.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="text-secondary text-center">No transactions available yet.</td></tr>`;
    return;
  }
  products.forEach(p => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.product}</td>
      <td class="text-right">${p.sales_count}</td>
      <td class="text-right bold">${formatCurrency(p.revenue)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderTopCustomers(customers) {
  const tbody = document.getElementById("top-customers-table");
  tbody.innerHTML = "";
  if (!customers.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="text-secondary text-center">No transactions available yet.</td></tr>`;
    return;
  }
  customers.forEach(c => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${c.customer}</td>
      <td class="text-right">${c.sales_count}</td>
      <td class="text-right bold">${formatCurrency(c.revenue)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// --- Report Downloading Streams ---
async function downloadReport(format) {
  const alertId = "report-alert";
  hideAlert(alertId);

  const startDate = document.getElementById("report-start-date").value;
  const endDate = document.getElementById("report-end-date").value;
  const product = document.getElementById("report-product").value;
  const customer = document.getElementById("report-customer").value;

  const params = new URLSearchParams();
  if (startDate) params.append("start_date", startDate);
  if (endDate) params.append("end_date", endDate);
  if (product) params.append("product", product);
  if (customer) params.append("customer", customer);

  const endpoint = `/report/${format}?${params.toString()}`;

  try {
    showAlert(alertId, "Preparing report download...", "success");
    const response = await authenticatedFetch("report", endpoint);

    if (!response.ok) {
      throw new Error("Unable to build report. Verify filters.");
    }

    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sales_report_${new Date().toISOString().slice(0,10)}.${format === "excel" ? "xlsx" : "csv"}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);

    showAlert(alertId, "Download triggered successfully!", "success");
  } catch (err) {
    showAlert(alertId, err.message, "error");
  }
}

// --- Event Listeners Mapping ---
function setupEventListeners() {
  // Auth Form tabs
  const tabLogin = document.getElementById("tab-login");
  const tabRegister = document.getElementById("tab-register");
  const formLogin = document.getElementById("login-form");
  const formRegister = document.getElementById("register-form");

  tabLogin.addEventListener("click", () => {
    tabLogin.classList.add("active");
    tabRegister.classList.remove("active");
    formLogin.classList.remove("hidden");
    formRegister.classList.add("hidden");
    hideAlert("auth-alert");
  });

  tabRegister.addEventListener("click", () => {
    tabRegister.classList.add("active");
    tabLogin.classList.remove("active");
    formRegister.classList.remove("hidden");
    formLogin.classList.add("hidden");
    hideAlert("auth-alert");
  });

  // Submit Operations
  formLogin.addEventListener("submit", handleLogin);
  formRegister.addEventListener("submit", handleRegister);
  document.getElementById("btn-logout").addEventListener("click", handleLogout);

  // Side navigation clicks
  document.getElementById("nav-analytics").addEventListener("click", () => switchPanel("analytics"));
  document.getElementById("nav-ingest").addEventListener("click", () => switchPanel("ingest"));
  document.getElementById("nav-reports").addEventListener("click", () => switchPanel("reports"));

  // File uploading drag/drop events
  const dropZone = document.getElementById("drop-zone");
  const fileInput = document.getElementById("file-input");

  dropZone.addEventListener("click", () => fileInput.click());
  
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
  
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    handleFileSelect(e);
  });
  
  fileInput.addEventListener("change", handleFileSelect);
  document.getElementById("btn-upload").addEventListener("click", handleFileUpload);

  // Database connector forms
  document.getElementById("db-connector-form").addEventListener("submit", handleDatabaseImport);
  document.getElementById("btn-db-test").addEventListener("click", testDatabaseConnection);
  
  // Custom port change listener depending on db selection
  document.getElementById("db-type").addEventListener("change", (e) => {
    const portField = document.getElementById("db-port");
    portField.value = e.target.value === "postgres" ? "5432" : "3306";
  });

  // Download export triggers
  document.getElementById("btn-export-csv").addEventListener("click", () => downloadReport("csv"));
  document.getElementById("btn-export-excel").addEventListener("click", () => downloadReport("excel"));
}

// --- Utility Functions ---
function parseJwt(token) {
  try {
    const base64Url = token.split(".")[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      window.atob(base64)
        .split("")
        .map(c => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    );
    return JSON.parse(jsonPayload);
  } catch (e) {
    return {};
  }
}

function formatCurrency(amount) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD"
  }).format(amount || 0);
}

function formatMonthName(yearMonthStr) {
  if (!yearMonthStr) return "-";
  const [year, month] = yearMonthStr.split("-");
  const dateObj = new Date(year, parseInt(month) - 1, 1);
  return dateObj.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

function showAlert(alertId, message, type = "success") {
  const alertEl = document.getElementById(alertId);
  alertEl.textContent = message;
  alertEl.className = `alert ${type}`;
  alertEl.classList.remove("hidden");
}

function hideAlert(alertId) {
  const alertEl = document.getElementById(alertId);
  alertEl.classList.add("hidden");
  alertEl.textContent = "";
}
