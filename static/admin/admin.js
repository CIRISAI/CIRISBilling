// CIRIS Billing Admin UI JavaScript
// Simple vanilla JS approach following CIRISManager pattern

// Configuration
const API_BASE_URL = window.location.origin; // Same origin as admin UI
const TOKEN_KEY = 'ciris_billing_admin_token';
const USER_KEY = 'ciris_billing_admin_user';

// State
let currentUser = null;
let currentTab = 'dashboard';
let users = [];
let apiKeys = [];

// ============================================================================
// Authentication - Google OAuth
// ============================================================================

function initiateGoogleLogin() {
    // Redirect to Google OAuth (return to admin-ui after login)
    const redirectUri = window.location.origin + '/admin-ui/';
    window.location.href = `${API_BASE_URL}/admin/oauth/login?redirect_uri=${encodeURIComponent(redirectUri)}`;
}

async function handleOAuthCallback() {
    // Check if we have a token in the URL (returned from OAuth callback)
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');

    if (token) {
        // Store token
        localStorage.setItem(TOKEN_KEY, token);

        // Remove token from URL (keep current path)
        window.history.replaceState({}, document.title, window.location.pathname);

        // Fetch user info
        try {
            const userData = await apiRequest('/admin/oauth/user');
            localStorage.setItem(USER_KEY, JSON.stringify(userData));
            currentUser = userData;
            showApp();
        } catch (error) {
            console.error('Failed to fetch user info:', error);
            showLoginError('Authentication failed. Please try again.');
            logout();
        }
    }
}

function showLoginError(message) {
    const errorDiv = document.getElementById('login-error');
    if (errorDiv) {
        errorDiv.textContent = message;
        errorDiv.classList.remove('hidden');
    } else {
        alert(message);
    }
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
    currentUser = null;

    // Hide app, show login
    document.getElementById('app').classList.add('hidden');
    document.getElementById('login-screen').classList.remove('hidden');
}

function checkAuth() {
    const token = localStorage.getItem(TOKEN_KEY);
    const userStr = localStorage.getItem(USER_KEY);

    if (token && userStr) {
        try {
            currentUser = JSON.parse(userStr);
            return true;
        } catch (e) {
            return false;
        }
    }
    return false;
}

// ============================================================================
// API Helpers
// ============================================================================

async function apiRequest(endpoint, options = {}) {
    const token = localStorage.getItem(TOKEN_KEY);

    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers
    });

    if (response.status === 401) {
        // Token expired
        logout();
        throw new Error('Session expired. Please login again.');
    }

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'API request failed');
    }

    return response.json();
}

// ============================================================================
// UI State Management
// ============================================================================

function switchTab(tabName) {
    // Hide all tabs
    const tabs = ['dashboard', 'users', 'api-keys', 'analytics', 'config'];
    tabs.forEach(tab => {
        document.getElementById(`${tab}-content`).classList.add('hidden');
        document.getElementById(`${tab}-tab`).classList.remove('tab-active');
        document.getElementById(`${tab}-tab`).classList.add('text-gray-500');
    });

    // Show selected tab
    document.getElementById(`${tabName}-content`).classList.remove('hidden');
    document.getElementById(`${tabName}-tab`).classList.add('tab-active');
    document.getElementById(`${tabName}-tab`).classList.remove('text-gray-500');

    currentTab = tabName;

    // Load data for tab
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

function showApp() {
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');

    // Update header with user info (API returns "name", not "full_name")
    document.getElementById('admin-name').textContent = currentUser.name || currentUser.email;
    document.getElementById('admin-role').textContent = currentUser.role.replace('_', ' ').toUpperCase();

    // Load dashboard
    loadTabData('dashboard');
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
    const alert = document.getElementById('success-alert');
    document.getElementById('success-message').textContent = message;
    alert.classList.remove('hidden');

    setTimeout(() => {
        alert.classList.add('hidden');
    }, 3000);
}

async function refreshData() {
    const icon = document.getElementById('refresh-icon');
    icon.classList.add('fa-spin');

    await loadTabData(currentTab);

    icon.classList.remove('fa-spin');
    showSuccess('Data refreshed');
}

// ============================================================================
// Dashboard
// ============================================================================

async function loadDashboard() {
    try {
        const data = await apiRequest('/admin/analytics/overview');

        // Update metrics cards (API returns flat structure)
        document.getElementById('total-users').textContent = data.total_users.toLocaleString();
        document.getElementById('revenue-today').textContent = formatMoney(data.total_charged_all_time);
        document.getElementById('active-today').textContent = data.active_users.toLocaleString();
        document.getElementById('total-charges').textContent = data.charges_last_24h.toLocaleString();

        // Update change percentages (show 24h vs 7d comparison)
        const charges24h = data.charges_last_24h;
        const avgDaily7d = data.charges_last_7d / 7;
        const changePercent = avgDaily7d > 0 ? ((charges24h - avgDaily7d) / avgDaily7d * 100) : 0;
        document.getElementById('revenue-change').textContent =
            `${changePercent > 0 ? '+' : ''}${changePercent.toFixed(1)}% vs 7d avg`;

        // Load recent activity
        await loadRecentActivity();

    } catch (error) {
        console.error('Dashboard load error:', error);
        // Show placeholder data on error
        document.getElementById('total-users').textContent = '-';
        document.getElementById('revenue-today').textContent = '-';
        document.getElementById('active-today').textContent = '-';
        document.getElementById('total-charges').textContent = '-';
    }
}

async function loadRecentActivity() {
    const activityDiv = document.getElementById('recent-activity');
    activityDiv.innerHTML = '<p class="text-gray-500 text-sm">No recent activity</p>';

    // TODO: Implement when activity endpoint is available
}

// ============================================================================
// Users Tab
// ============================================================================

async function loadUsers() {
    try {
        const data = await apiRequest('/admin/users?page=1&page_size=100');
        users = data.users;

        // API returns flat structure: data.total, data.page, data.page_size, data.total_pages
        document.getElementById('user-count').textContent = data.total;
        renderUsersTable(users);

    } catch (error) {
        console.error('Users load error:', error);
        document.getElementById('users-table').innerHTML =
            '<p class="text-red-500">Failed to load users. Admin endpoints may not be implemented yet.</p>';
    }
}

function renderUsersTable(usersList) {
    const tableDiv = document.getElementById('users-table');

    if (usersList.length === 0) {
        tableDiv.innerHTML = '<p class="text-gray-500">No users found</p>';
        return;
    }

    const html = `
        <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
                <tr>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">User</th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Plan</th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Credits</th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Uses</th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Created</th>
                    <th class="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                </tr>
            </thead>
            <tbody class="bg-white divide-y divide-gray-200">
                ${usersList.map(user => `
                    <tr class="hover:bg-gray-50 cursor-pointer" onclick="viewUser('${user.account_id}')">
                        <td class="px-4 py-4">
                            <div class="flex items-center">
                                <div class="flex-shrink-0 h-10 w-10 bg-blue-100 rounded-full flex items-center justify-center">
                                    <span class="text-blue-600 font-medium text-sm">${getUserInitials(user)}</span>
                                </div>
                                <div class="ml-3">
                                    <div class="text-sm font-medium text-gray-900">${getUserDisplayName(user)}</div>
                                    <div class="text-xs text-gray-500">${getProviderIcon(user.oauth_provider)} ${truncateId(user.external_id)}</div>
                                </div>
                            </div>
                        </td>
                        <td class="px-4 py-4 whitespace-nowrap">
                            <span class="px-2 py-1 text-xs font-semibold rounded-full ${getPlanBadge(user.plan_name)}">
                                ${user.plan_name}
                            </span>
                        </td>
                        <td class="px-4 py-4 whitespace-nowrap">
                            <div class="text-sm text-gray-900">${user.free_uses_remaining} free</div>
                            <div class="text-xs text-gray-500">${user.paid_credits} paid</div>
                        </td>
                        <td class="px-4 py-4 whitespace-nowrap">
                            <div class="text-sm font-medium text-gray-900">${user.total_uses}</div>
                            <div class="text-xs text-gray-500">${user.charge_count} charges</div>
                        </td>
                        <td class="px-4 py-4 whitespace-nowrap">
                            <span class="px-2 py-1 text-xs font-semibold rounded-full ${getStatusBadge(user.status)}">
                                ${user.status}
                            </span>
                        </td>
                        <td class="px-4 py-4 whitespace-nowrap text-sm text-gray-500">
                            ${formatDateShort(user.created_at)}
                        </td>
                        <td class="px-4 py-4 whitespace-nowrap text-sm" onclick="event.stopPropagation()">
                            <button onclick="viewUser('${user.account_id}')" class="text-blue-600 hover:text-blue-900 font-medium">
                                <i class="fas fa-eye mr-1"></i>View
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;

    tableDiv.innerHTML = html;
}

function getUserDisplayName(user) {
    if (user.customer_email) {
        return user.customer_email;
    }
    // For Google OAuth, external_id is numeric - show truncated
    return truncateId(user.external_id);
}

function getUserInitials(user) {
    if (user.customer_email) {
        const parts = user.customer_email.split('@')[0].split(/[._-]/);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[1][0]).toUpperCase();
        }
        return user.customer_email.substring(0, 2).toUpperCase();
    }
    return user.external_id.substring(0, 2).toUpperCase();
}

function truncateId(id) {
    if (!id) return '-';
    if (id.length <= 12) return id;
    return id.substring(0, 6) + '...' + id.substring(id.length - 4);
}

function getProviderIcon(provider) {
    if (provider.includes('google')) return '<i class="fab fa-google text-red-500"></i>';
    if (provider.includes('discord')) return '<i class="fab fa-discord text-indigo-500"></i>';
    if (provider.includes('github')) return '<i class="fab fa-github text-gray-700"></i>';
    return '<i class="fas fa-user text-gray-400"></i>';
}

function getPlanBadge(plan) {
    switch (plan?.toLowerCase()) {
        case 'pro':
        case 'premium':
            return 'bg-purple-100 text-purple-800';
        case 'business':
        case 'enterprise':
            return 'bg-indigo-100 text-indigo-800';
        default:
            return 'bg-gray-100 text-gray-800';
    }
}

function formatDateShort(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    const now = new Date();
    const diffDays = Math.floor((now - date) / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;

    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function filterUsers() {
    const searchTerm = document.getElementById('user-search').value.toLowerCase();
    const statusFilter = document.getElementById('status-filter').value;

    const filtered = users.filter(user => {
        const matchesSearch = user.external_id.toLowerCase().includes(searchTerm) ||
                            user.oauth_provider.toLowerCase().includes(searchTerm) ||
                            (user.customer_email && user.customer_email.toLowerCase().includes(searchTerm));
        const matchesStatus = !statusFilter || user.status === statusFilter;

        return matchesSearch && matchesStatus;
    });

    renderUsersTable(filtered);
}

async function viewUser(accountId) {
    // Show modal with loading state
    document.getElementById('user-detail-modal').classList.remove('hidden');
    document.getElementById('user-detail-content').innerHTML = `
        <div class="flex items-center justify-center py-12">
            <div class="loader"></div>
        </div>
    `;

    try {
        const user = await apiRequest(`/admin/users/${accountId}`);
        renderUserDetail(user);
    } catch (error) {
        console.error('Error loading user details:', error);
        document.getElementById('user-detail-content').innerHTML = `
            <div class="text-center py-12">
                <i class="fas fa-exclamation-circle text-red-500 text-4xl mb-4"></i>
                <p class="text-gray-600">Failed to load user details</p>
                <p class="text-sm text-gray-400 mt-2">${error.message}</p>
            </div>
        `;
    }
}

function renderUserDetail(user) {
    const html = `
        <!-- User Header -->
        <div class="flex items-start justify-between mb-6">
            <div class="flex items-center">
                <div class="h-16 w-16 bg-blue-100 rounded-full flex items-center justify-center mr-4">
                    <span class="text-blue-600 font-bold text-xl">${getUserInitials(user)}</span>
                </div>
                <div>
                    <h3 class="text-xl font-bold text-gray-900">${getUserDisplayName(user)}</h3>
                    <p class="text-sm text-gray-500">${getProviderIcon(user.oauth_provider)} ${user.oauth_provider}</p>
                    <p class="text-xs text-gray-400 font-mono mt-1">${user.external_id}</p>
                </div>
            </div>
            <span class="px-3 py-1 text-sm font-semibold rounded-full ${getStatusBadge(user.status)}">
                ${user.status}
            </span>
        </div>

        <!-- Stats Grid -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-blue-50 p-4 rounded-lg">
                <p class="text-xs text-blue-600 font-medium uppercase">Free Credits</p>
                <p class="text-2xl font-bold text-blue-900">${user.free_uses_remaining}</p>
            </div>
            <div class="bg-green-50 p-4 rounded-lg">
                <p class="text-xs text-green-600 font-medium uppercase">Paid Credits</p>
                <p class="text-2xl font-bold text-green-900">${user.paid_credits}</p>
            </div>
            <div class="bg-purple-50 p-4 rounded-lg">
                <p class="text-xs text-purple-600 font-medium uppercase">Total Uses</p>
                <p class="text-2xl font-bold text-purple-900">${user.total_uses}</p>
            </div>
            <div class="bg-orange-50 p-4 rounded-lg">
                <p class="text-xs text-orange-600 font-medium uppercase">Balance</p>
                <p class="text-2xl font-bold text-orange-900">${formatMoney(user.balance_minor)}</p>
            </div>
        </div>

        <!-- Details Grid -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <!-- Account Info -->
            <div class="bg-gray-50 p-4 rounded-lg">
                <h4 class="font-semibold text-gray-900 mb-3"><i class="fas fa-user mr-2"></i>Account Info</h4>
                <dl class="space-y-2 text-sm">
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Account ID</dt>
                        <dd class="text-gray-900 font-mono text-xs">${user.account_id}</dd>
                    </div>
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Plan</dt>
                        <dd><span class="px-2 py-0.5 text-xs font-semibold rounded-full ${getPlanBadge(user.plan_name)}">${user.plan_name}</span></dd>
                    </div>
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Currency</dt>
                        <dd class="text-gray-900">${user.currency}</dd>
                    </div>
                    ${user.customer_email ? `
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Email</dt>
                        <dd class="text-gray-900">${user.customer_email}</dd>
                    </div>` : ''}
                    ${user.tenant_id ? `
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Tenant ID</dt>
                        <dd class="text-gray-900 font-mono text-xs">${user.tenant_id}</dd>
                    </div>` : ''}
                    ${user.wa_id ? `
                    <div class="flex justify-between">
                        <dt class="text-gray-500">WA ID</dt>
                        <dd class="text-gray-900 font-mono text-xs">${user.wa_id}</dd>
                    </div>` : ''}
                </dl>
            </div>

            <!-- Activity Info -->
            <div class="bg-gray-50 p-4 rounded-lg">
                <h4 class="font-semibold text-gray-900 mb-3"><i class="fas fa-clock mr-2"></i>Activity</h4>
                <dl class="space-y-2 text-sm">
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Created</dt>
                        <dd class="text-gray-900">${formatDate(user.created_at)}</dd>
                    </div>
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Last Charge</dt>
                        <dd class="text-gray-900">${user.last_charge_at ? formatDate(user.last_charge_at) : 'Never'}</dd>
                    </div>
                    <div class="flex justify-between">
                        <dt class="text-gray-500">Last Credit</dt>
                        <dd class="text-gray-900">${user.last_credit_at ? formatDate(user.last_credit_at) : 'Never'}</dd>
                    </div>
                </dl>
            </div>
        </div>

        <!-- Transaction Summary -->
        <div class="bg-gray-50 p-4 rounded-lg mb-6">
            <h4 class="font-semibold text-gray-900 mb-3"><i class="fas fa-receipt mr-2"></i>Transaction Summary</h4>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                    <p class="text-gray-500">Total Charged</p>
                    <p class="text-lg font-semibold text-red-600">${formatMoney(user.total_charged)}</p>
                </div>
                <div>
                    <p class="text-gray-500">Total Credited</p>
                    <p class="text-lg font-semibold text-green-600">${formatMoney(user.total_credited)}</p>
                </div>
                <div>
                    <p class="text-gray-500">Charge Count</p>
                    <p class="text-lg font-semibold text-gray-900">${user.charge_count}</p>
                </div>
                <div>
                    <p class="text-gray-500">Credit Count</p>
                    <p class="text-lg font-semibold text-gray-900">${user.credit_count}</p>
                </div>
            </div>
        </div>

        <!-- Actions -->
        <div class="flex justify-end gap-3 pt-4 border-t">
            <button onclick="hideUserDetailModal()" class="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50">
                Close
            </button>
        </div>
    `;

    document.getElementById('user-detail-content').innerHTML = html;
}

function hideUserDetailModal() {
    document.getElementById('user-detail-modal').classList.add('hidden');
}

// ============================================================================
// API Keys Tab
// ============================================================================

async function loadAPIKeys() {
    try {
        // API returns a direct array, not wrapped in { api_keys: [] }
        const data = await apiRequest('/admin/api-keys');
        apiKeys = Array.isArray(data) ? data : [];

        document.getElementById('api-key-count').textContent = apiKeys.length;
        renderAPIKeys(apiKeys);

    } catch (error) {
        console.error('API Keys load error:', error);
        document.getElementById('api-keys-list').innerHTML =
            '<p class="text-red-500">Failed to load API keys. Admin endpoints may not be implemented yet.</p>';
    }
}

function renderAPIKeys(keys) {
    const listDiv = document.getElementById('api-keys-list');

    if (keys.length === 0) {
        listDiv.innerHTML = '<p class="text-gray-500">No API keys found. Create one to get started.</p>';
        return;
    }

    const html = keys.map(key => `
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
            '<p class="text-red-500">Failed to load analytics. Admin endpoints may not be implemented yet.</p>';
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

// ============================================================================
// Initialization
// ============================================================================

window.addEventListener('DOMContentLoaded', async () => {
    // Ensure all screens start hidden except loading
    document.getElementById('loading').classList.remove('hidden');
    document.getElementById('login-screen').classList.add('hidden');
    document.getElementById('app').classList.add('hidden');

    // Check if we're returning from OAuth callback
    const urlParams = new URLSearchParams(window.location.search);
    const hasToken = urlParams.get('token');

    if (hasToken) {
        // Handle OAuth callback
        await handleOAuthCallback();
    } else if (checkAuth()) {
        // Already authenticated
        showApp();
    } else {
        // Show login screen
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('login-screen').classList.remove('hidden');
    }
});
