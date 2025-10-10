// CIRIS Billing Admin Dashboard JavaScript
// Requires authentication - redirects to login if not authenticated

// Configuration
const API_BASE_URL = window.location.origin;
const TOKEN_KEY = 'ciris_billing_admin_token';
const USER_KEY = 'ciris_billing_admin_user';

// State
let currentUser = null;
let currentTab = 'dashboard';
let users = [];
let apiKeys = [];

// ============================================================================
// Auth Check & Initialization
// ============================================================================

// Check authentication on page load
(function checkAuth() {
    const token = localStorage.getItem(TOKEN_KEY);
    const userStr = localStorage.getItem(USER_KEY);

    if (!token || !userStr) {
        // Not authenticated - redirect to login
        window.location.href = '/';
        return;
    }

    try {
        currentUser = JSON.parse(userStr);
        initializeApp();
    } catch (e) {
        // Invalid user data - redirect to login
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        window.location.href = '/';
    }
})();

function initializeApp() {
    // Update header with user info
    document.getElementById('admin-name').textContent = currentUser.full_name;
    document.getElementById('admin-role').textContent = currentUser.role.replace('_', ' ').toUpperCase();

    // Initialize tab visibility and load dashboard
    switchTab('dashboard');
}

async function logout() {
    // Call logout endpoint
    try {
        await fetch(`${API_BASE_URL}/admin/oauth/logout`, {
            method: 'POST',
            credentials: 'include',
        });
    } catch (error) {
        console.error('Logout error:', error);
    }

    // Clear local storage
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);

    // Redirect to login
    window.location.href = '/';
}

// ============================================================================
// API Helpers
// ============================================================================

async function apiRequest(endpoint, options = {}) {
    const token = localStorage.getItem(TOKEN_KEY);

    const headers = {
        'Content-Type': 'application/json',
        ...options.headers,
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers,
        credentials: 'include',
    });

    if (response.status === 401) {
        // Unauthorized - redirect to login
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        window.location.href = '/';
        throw new Error('Unauthorized');
    }

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(error.detail || `HTTP ${response.status}`);
    }

    // Handle 204 No Content - no response body
    if (response.status === 204) {
        return null;
    }

    return response.json();
}

// ============================================================================
// Tab Management
// ============================================================================

function switchTab(tabName) {
    currentTab = tabName;

    // Update tab buttons
    const tabs = ['dashboard', 'users', 'api-keys', 'analytics', 'config'];
    tabs.forEach(tab => {
        const button = document.querySelector(`[onclick="switchTab('${tab}')"]`);
        if (button) {
            if (tab === tabName) {
                button.classList.add('tab-active', 'border-b-2');
            } else {
                button.classList.remove('tab-active', 'border-b-2');
            }
        }
    });

    // Show/hide tab content
    tabs.forEach(tab => {
        const content = document.getElementById(`${tab}-content`);
        if (content) {
            if (tab === tabName) {
                content.classList.remove('hidden');
                content.style.display = 'block';
            } else {
                content.classList.add('hidden');
                content.style.display = 'none';
            }
        }
    });

    // Load data for the tab
    loadTabData(tabName);
}

async function loadTabData(tabName) {
    try {
        switch (tabName) {
            case 'dashboard':
                await loadDashboard();
                break;
            case 'users':
                await loadUsers();
                break;
            case 'api-keys':
                await loadAPIKeys();
                break;
            case 'analytics':
                await loadAnalytics('daily');
                break;
            case 'config':
                await loadConfig();
                break;
        }
    } catch (error) {
        console.error(`Error loading ${tabName}:`, error);
        showError(error.message);
    }
}

function showError(message) {
    const alert = document.getElementById('error-alert');
    document.getElementById('error-message').textContent = message;
    alert.classList.remove('hidden');

    setTimeout(() => {
        alert.classList.add('hidden');
    }, 5000);
}

function showSuccess(message) {
    // Create temporary success alert
    const alert = document.createElement('div');
    alert.className = 'fixed top-4 right-4 bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded shadow-lg z-50';
    alert.textContent = message;
    document.body.appendChild(alert);

    setTimeout(() => {
        alert.remove();
    }, 3000);
}

async function refreshData() {
    const icon = document.getElementById('refresh-icon');
    icon.classList.add('fa-spin');

    try {
        await loadTabData(currentTab);
    } finally {
        icon.classList.remove('fa-spin');
    }
}

// ============================================================================
// Dashboard Tab
// ============================================================================

async function loadDashboard() {
    try {
        const stats = await apiRequest('/admin/analytics/overview');

        document.getElementById('total-users').textContent = stats.total_users.toLocaleString();
        document.getElementById('active-today').textContent = stats.active_users.toLocaleString();
        document.getElementById('revenue-today').textContent = formatMoney(stats.total_charged_all_time);
        document.getElementById('total-charges').textContent = stats.charges_last_24h.toLocaleString();

    } catch (error) {
        console.error('Dashboard load error:', error);
        // Set placeholder values on error
        const safeSet = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        safeSet('total-users', '-');
        safeSet('active-today', '-');
        safeSet('revenue-today', '-');
        safeSet('total-charges', '-');
    }
}

// ============================================================================
// Users Tab
// ============================================================================

async function loadUsers() {
    try {
        const data = await apiRequest('/admin/users');
        users = data.users || [];
        renderUsers();

    } catch (error) {
        console.error('Users load error:', error);
        document.getElementById('users-table').innerHTML =
            '<p class="text-red-500">Failed to load users. Admin endpoints may not be implemented yet.</p>';
    }
}

function renderUsers() {
    const listDiv = document.getElementById('users-table');

    if (users.length === 0) {
        listDiv.innerHTML = '<p class="text-gray-500">No users found</p>';
        return;
    }

    const html = users.map(user => `
        <div class="bg-white p-4 rounded-lg border">
            <div class="flex justify-between items-start">
                <div>
                    <h3 class="text-lg font-semibold">${user.external_id} (${user.oauth_provider})</h3>
                    <div class="mt-2 text-sm text-gray-600">
                        <p><strong>Balance:</strong> ${formatMoney(user.balance_minor)}</p>
                        <p><strong>Plan:</strong> ${user.plan_name}</p>
                        <p><strong>Total Charged:</strong> ${formatMoney(user.total_charged)}</p>
                        <p><strong>Total Credited:</strong> ${formatMoney(user.total_credited)}</p>
                        <p><strong>Status:</strong> <span class="px-2 py-1 text-xs font-semibold rounded-full ${getStatusBadge(user.status)}">${user.status}</span></p>
                        <p><strong>Created:</strong> ${formatDate(user.created_at)}</p>
                    </div>
                </div>
                <div>
                    <button onclick="viewUserDetails('${user.account_id}')"
                            class="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
                        View Details
                    </button>
                </div>
            </div>
        </div>
    `).join('');

    listDiv.innerHTML = html;
}

async function viewUserDetails(userId) {
    try {
        const user = await apiRequest(`/admin/users/${userId}`);
        alert(`User: ${user.external_id}\nBalance: ${formatMoney(user.balance_minor)}\nStatus: ${user.status}\nPlan: ${user.plan_name}`);
    } catch (error) {
        showError('Failed to load user details');
    }
}

// ============================================================================
// API Keys Tab
// ============================================================================

async function loadAPIKeys() {
    try {
        const data = await apiRequest('/admin/api-keys');
        apiKeys = data || [];  // API returns array directly, not {keys: [...]}

        // Update count display
        const countEl = document.getElementById('api-key-count');
        if (countEl) countEl.textContent = apiKeys.length;

        renderAPIKeys();

    } catch (error) {
        console.error('API keys load error:', error);
        document.getElementById('api-keys-list').innerHTML =
            '<p class="text-red-500">Failed to load API keys.</p>';
    }
}

function renderAPIKeys() {
    const listDiv = document.getElementById('api-keys-list');

    if (apiKeys.length === 0) {
        listDiv.innerHTML = '<p class="text-gray-500">No API keys found. Create one to get started.</p>';
        return;
    }

    const html = apiKeys.map(key => `
        <div class="bg-white p-4 rounded-lg border">
            <div class="flex justify-between items-start">
                <div class="flex-1">
                    <div class="flex items-center gap-2">
                        <h3 class="text-lg font-semibold">${key.name}</h3>
                        <span class="px-2 py-1 text-xs font-semibold rounded-full ${getEnvBadge(key.environment)}">
                            ${key.environment}
                        </span>
                        <span class="px-2 py-1 text-xs font-semibold rounded-full ${getKeyStatusBadge(key.status)}">
                            ${key.status}
                        </span>
                    </div>
                    <p class="text-sm text-gray-600 mt-1 api-key-mono">${key.key_prefix}...</p>
                    <div class="mt-2 text-sm text-gray-500">
                        <p><strong>Permissions:</strong> ${key.permissions.join(', ')}</p>
                        <p><strong>Last Used:</strong> ${key.last_used_at ? formatDate(key.last_used_at) : 'Never'}</p>
                        <p><strong>Created:</strong> ${formatDate(key.created_at)}</p>
                        ${key.expires_at ? `<p><strong>Expires:</strong> ${formatDate(key.expires_at)}</p>` : ''}
                    </div>
                </div>
                <div class="flex gap-2">
                    <button onclick="rotateAPIKey('${key.id}')"
                            class="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
                        <i class="fas fa-sync-alt"></i> Rotate
                    </button>
                    <button onclick="revokeAPIKey('${key.id}')"
                            class="px-3 py-1 text-sm bg-red-600 text-white rounded hover:bg-red-700">
                        <i class="fas fa-ban"></i> Revoke
                    </button>
                </div>
            </div>
        </div>
    `).join('');

    listDiv.innerHTML = html;
}

function showCreateKeyDialog() {
    const modal = document.getElementById('create-key-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

function hideCreateKeyDialog() {
    const modal = document.getElementById('create-key-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
    document.getElementById('create-key-form').reset();
}

async function createAPIKey(event) {
    event.preventDefault();

    const name = document.getElementById('key-name').value;
    const description = document.getElementById('key-description').value;
    const environment = document.getElementById('key-environment').value;
    const expiresIn = document.getElementById('key-expires').value;

    try {
        const data = await apiRequest('/admin/api-keys', {
            method: 'POST',
            body: JSON.stringify({
                name,
                description: description || null,
                environment,
                expires_in_days: expiresIn ? parseInt(expiresIn) : null
            })
        });

        hideCreateKeyDialog();
        showGeneratedKey(data);
        await loadAPIKeys();

    } catch (error) {
        console.error('Create API key error:', error);
        showError(error.message);
    }
}

function showGeneratedKey(keyData) {
    document.getElementById('generated-key').value = keyData.plaintext_key;
    document.getElementById('key-modal-name').textContent = keyData.name;
    document.getElementById('key-modal-env').textContent = keyData.environment;
    document.getElementById('key-modal-perms').textContent = keyData.permissions.join(', ');
    const modal = document.getElementById('show-key-modal');
    modal.classList.remove('hidden');
    modal.style.display = 'flex';
}

function hideShowKeyDialog() {
    const modal = document.getElementById('show-key-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

function copyAPIKey() {
    const input = document.getElementById('generated-key');
    input.select();
    document.execCommand('copy');
    showSuccess('API key copied to clipboard');
}

async function rotateAPIKey(keyId) {
    if (!confirm('Rotate this API key? The old key will be valid for 24 hours.')) {
        return;
    }

    try {
        const data = await apiRequest(`/admin/api-keys/${keyId}/rotate`, {
            method: 'POST'
        });

        showGeneratedKey(data.new_key);
        showSuccess('API key rotated successfully');
        await loadAPIKeys();

    } catch (error) {
        console.error('Rotate API key error:', error);
        showError(error.message);
    }
}

async function revokeAPIKey(keyId) {
    if (!confirm('Revoke this API key? This action cannot be undone.')) {
        return;
    }

    try {
        await apiRequest(`/admin/api-keys/${keyId}`, {
            method: 'DELETE'
        });

        showSuccess('API key revoked');
        await loadAPIKeys();

    } catch (error) {
        console.error('Revoke API key error:', error);
        showError(error.message);
    }
}

// ============================================================================
// Analytics Tab
// ============================================================================

function switchAnalyticsView(view) {
    const buttons = ['daily', 'weekly', 'monthly', 'all-time'];
    buttons.forEach(btn => {
        const button = document.getElementById(`${btn}-btn`);
        if (btn === view) {
            button.classList.remove('bg-gray-200', 'text-gray-700');
            button.classList.add('bg-blue-600', 'text-white');
        } else {
            button.classList.remove('bg-blue-600', 'text-white');
            button.classList.add('bg-gray-200', 'text-gray-700');
        }
    });

    loadAnalytics(view);
}

async function loadAnalytics(view) {
    try {
        const endpoint = view === 'all-time' ?
            '/admin/analytics/all-time' :
            `/admin/analytics/${view}`;

        const data = await apiRequest(endpoint);
        renderAnalytics(data, view);

    } catch (error) {
        console.error('Analytics load error:', error);
        document.getElementById('analytics-data').innerHTML =
            '<p class="text-red-500">Failed to load analytics.</p>';
    }
}

function renderAnalytics(data, view) {
    const analyticsDiv = document.getElementById('analytics-data');

    if (view === 'all-time') {
        analyticsDiv.innerHTML = `
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-white p-6 rounded-lg border">
                    <h3 class="text-lg font-semibold mb-2">Total Users</h3>
                    <p class="text-4xl font-bold text-blue-600">${data.all_time.total_users.toLocaleString()}</p>
                </div>
                <div class="bg-white p-6 rounded-lg border">
                    <h3 class="text-lg font-semibold mb-2">Total Revenue</h3>
                    <p class="text-4xl font-bold text-green-600">${formatMoney(data.all_time.total_revenue_minor)}</p>
                </div>
                <div class="bg-white p-6 rounded-lg border">
                    <h3 class="text-lg font-semibold mb-2">Total Charges</h3>
                    <p class="text-4xl font-bold text-purple-600">${data.all_time.total_charges.toLocaleString()}</p>
                </div>
                <div class="bg-white p-6 rounded-lg border">
                    <h3 class="text-lg font-semibold mb-2">Avg User LTV</h3>
                    <p class="text-4xl font-bold text-orange-600">${formatMoney(data.all_time.avg_user_lifetime_value_minor)}</p>
                </div>
            </div>
        `;
    } else {
        analyticsDiv.innerHTML = `<p class="text-gray-500">Analytics data for ${view} view will be displayed here</p>`;
    }
}

// ============================================================================
// Configuration Tab
// ============================================================================

async function loadConfig() {
    try {
        // TODO: Implement /admin/config/billing endpoint
        // For now, show placeholder message
        console.log('Config endpoint not yet implemented');
        // Leave default values
    } catch (error) {
        console.error('Config load error:', error);
        // Leave default values
    }
}

async function saveBillingConfig() {
    const config = {
        pricing: {
            free_uses_per_account: parseInt(document.getElementById('free-uses').value),
            paid_uses_per_purchase: parseInt(document.getElementById('paid-uses').value),
            price_per_purchase_minor: parseInt(document.getElementById('price-cents').value)
        }
    };

    try {
        await apiRequest('/admin/config/billing', {
            method: 'PUT',
            body: JSON.stringify(config)
        });

        showSuccess('Billing configuration saved');

    } catch (error) {
        console.error('Save billing config error:', error);
        showError(error.message);
    }
}

async function saveStripeConfig() {
    const configData = {
        api_key: document.getElementById('stripe-secret-key').value,
        webhook_secret: document.getElementById('stripe-webhook-secret').value,
        publishable_key: document.getElementById('stripe-pub-key').value
    };

    try {
        await apiRequest('/admin/config/providers/stripe', {
            method: 'PUT',
            body: JSON.stringify({
                config_data: configData
            })
        });

        showSuccess('Stripe configuration saved');

    } catch (error) {
        console.error('Save Stripe config error:', error);
        showError(error.message);
    }
}

// ============================================================================
// Utility Functions
// ============================================================================

function formatMoney(minorUnits) {
    if (minorUnits === null || minorUnits === undefined) return '-';
    return `$${(minorUnits / 100).toFixed(2)}`;
}

function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

function getStatusBadge(status) {
    switch (status) {
        case 'active':
            return 'bg-green-100 text-green-800';
        case 'suspended':
            return 'bg-red-100 text-red-800';
        case 'closed':
            return 'bg-gray-100 text-gray-800';
        default:
            return 'bg-gray-100 text-gray-800';
    }
}

function getEnvBadge(env) {
    return env === 'live' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800';
}

function getKeyStatusBadge(status) {
    switch (status) {
        case 'active':
            return 'bg-green-100 text-green-800';
        case 'rotating':
            return 'bg-yellow-100 text-yellow-800';
        case 'revoked':
            return 'bg-red-100 text-red-800';
        default:
            return 'bg-gray-100 text-gray-800';
    }
}
