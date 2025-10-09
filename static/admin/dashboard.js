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

    // Load dashboard
    loadTabData('dashboard');
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
        const content = document.getElementById(`${tab}-tab`);
        if (content) {
            if (tab === tabName) {
                content.classList.remove('hidden');
            } else {
                content.classList.add('hidden');
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
        const stats = await apiRequest('/admin/stats');

        document.getElementById('total-users').textContent = stats.total_users.toLocaleString();
        document.getElementById('active-users').textContent = stats.active_accounts.toLocaleString();
        document.getElementById('total-revenue').textContent = formatMoney(stats.total_revenue_minor);
        document.getElementById('total-charges').textContent = stats.total_charges.toLocaleString();

    } catch (error) {
        console.error('Dashboard load error:', error);
        // Set placeholder values
        document.getElementById('total-users').textContent = '-';
        document.getElementById('active-users').textContent = '-';
        document.getElementById('total-revenue').textContent = '-';
        document.getElementById('total-charges').textContent = '-';
    }
}

// ============================================================================
// Users Tab
// ============================================================================

async function loadUsers() {
    try {
        const data = await apiRequest('/admin/accounts');
        users = data.accounts || [];
        renderUsers();

    } catch (error) {
        console.error('Users load error:', error);
        document.getElementById('users-list').innerHTML =
            '<p class="text-red-500">Failed to load users. Admin endpoints may not be implemented yet.</p>';
    }
}

function renderUsers() {
    const listDiv = document.getElementById('users-list');

    if (users.length === 0) {
        listDiv.innerHTML = '<p class="text-gray-500">No users found</p>';
        return;
    }

    const html = users.map(user => `
        <div class="bg-white p-4 rounded-lg border">
            <div class="flex justify-between items-start">
                <div>
                    <h3 class="text-lg font-semibold">${user.email}</h3>
                    <div class="mt-2 text-sm text-gray-600">
                        <p><strong>Credits:</strong> ${user.credits_remaining}</p>
                        <p><strong>Free Uses:</strong> ${user.free_uses_remaining}</p>
                        <p><strong>Total Uses:</strong> ${user.total_uses}</p>
                        <p><strong>Status:</strong> <span class="px-2 py-1 text-xs font-semibold rounded-full ${getStatusBadge(user.status)}">${user.status}</span></p>
                        <p><strong>Created:</strong> ${formatDate(user.created_at)}</p>
                    </div>
                </div>
                <div>
                    <button onclick="viewUserDetails('${user.id}')"
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
        const user = await apiRequest(`/admin/accounts/${userId}`);
        alert(`User: ${user.email}\nCredits: ${user.credits_remaining}\nStatus: ${user.status}`);
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
        apiKeys = data.keys || [];
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
    document.getElementById('create-key-modal').classList.remove('hidden');
}

function hideCreateKeyDialog() {
    document.getElementById('create-key-modal').classList.add('hidden');
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
    document.getElementById('generated-key').value = keyData.api_key;
    document.getElementById('key-modal-name').textContent = keyData.name;
    document.getElementById('key-modal-env').textContent = keyData.environment;
    document.getElementById('key-modal-perms').textContent = keyData.permissions.join(', ');
    document.getElementById('show-key-modal').classList.remove('hidden');
}

function hideShowKeyDialog() {
    document.getElementById('show-key-modal').classList.add('hidden');
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
        const data = await apiRequest('/admin/config/billing');

        document.getElementById('free-uses').value = data.pricing.free_uses_per_account;
        document.getElementById('paid-uses').value = data.pricing.paid_uses_per_purchase;
        document.getElementById('price-cents').value = data.pricing.price_per_purchase_minor;
        document.getElementById('currency').value = data.pricing.currency;

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
    const config = {
        api_key: document.getElementById('stripe-secret-key').value,
        webhook_secret: document.getElementById('stripe-webhook-secret').value,
        publishable_key: document.getElementById('stripe-pub-key').value
    };

    try {
        await apiRequest('/admin/config/providers/stripe', {
            method: 'PUT',
            body: JSON.stringify(config)
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
