/**
 * Varia Law - Matter Tracker
 * Client-side JavaScript
 */

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', function() {
    const flashMessages = document.querySelectorAll('.flash');
    flashMessages.forEach(function(flash) {
        setTimeout(function() {
            flash.style.opacity = '0';
            flash.style.transform = 'translateY(-10px)';
            setTimeout(function() {
                flash.remove();
            }, 300);
        }, 5000);
    });
});

// Confirm delete actions
document.addEventListener('submit', function(e) {
    if (e.target.classList.contains('delete-form')) {
        if (!confirm('Are you sure you want to delete this? This action cannot be undone.')) {
            e.preventDefault();
        }
    }
});

// Modal handling
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
    }
}

// Close modal on backdrop click
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal-backdrop')) {
        e.target.classList.remove('active');
    }
});

// Close modal on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-backdrop.active').forEach(function(modal) {
            modal.classList.remove('active');
        });
    }
});

// Format currency inputs
document.querySelectorAll('input[data-currency]').forEach(function(input) {
    input.addEventListener('blur', function() {
        const value = parseFloat(this.value);
        if (!isNaN(value)) {
            this.value = value.toFixed(2);
        }
    });
});

// Auto-set today's date for date inputs if empty
document.querySelectorAll('input[type="date"][data-default-today]').forEach(function(input) {
    if (!input.value) {
        const today = new Date().toISOString().split('T')[0];
        input.value = today;
    }
});

// Quick status change confirmation (Matter "Set status..." form)
// Note: confirm on submit to preserve the current "select + click Set" flow.
document.addEventListener('submit', function(e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;

    if (form.classList.contains('quick-action') || form.classList.contains('quick-status-change')) {
        const select = form.querySelector('select[name="status"]');
        if (!select) return;

        if (!select.value) {
            e.preventDefault();
            return;
        }

        const label = (select.options && select.selectedIndex >= 0)
            ? (select.options[select.selectedIndex]?.text || select.value)
            : select.value;

        if (!confirm('Change status to "' + label + '"?')) {
            e.preventDefault();
            select.value = select.dataset.original || '';
        }
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + K to focus search (if on search page)
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        const searchInput = document.querySelector('input[name="q"]');
        if (searchInput) {
            e.preventDefault();
            searchInput.focus();
        }
    }
});

// Matter form enhancements (client rate autofill + fixed fee toggle)
document.addEventListener('DOMContentLoaded', function() {
    const clientSelect = document.getElementById('client_id');
    const rateInput = document.getElementById('rate');
    const hoursInput = document.getElementById('hours_billed_amount');
    const fixedFeeCheckbox = document.getElementById('fixed_fee_enabled');

    const sanitizeNumeric = function(value) {
        if (!value) return '';
        const cleaned = String(value).replace(/[^0-9.]/g, '');
        if (cleaned === '.' || cleaned === '') return '';
        const parts = cleaned.split('.');
        if (parts.length > 2) return parts[0] + '.' + parts.slice(1).join('');
        return cleaned;
    };

    const setReadonlyState = function(input, isDisabled) {
        if (!input) return;
        input.readOnly = isDisabled;
        input.classList.toggle('is-disabled', isDisabled);
        input.setAttribute('aria-disabled', isDisabled ? 'true' : 'false');
    };

    const applyClientRate = function(force) {
        if (!clientSelect || !rateInput) return;
        if (rateInput.dataset.autofillEnabled !== 'true') return;

        const option = clientSelect.options[clientSelect.selectedIndex];
        const defaultRate = sanitizeNumeric(option ? option.dataset.defaultRate : '');
        if (!defaultRate && !force) return;

        const userEdited = rateInput.dataset.userEdited === 'true';
        if (userEdited && rateInput.value !== '' && !force) return;

        rateInput.value = defaultRate;
    };

    if (clientSelect && rateInput) {
        clientSelect.addEventListener('change', function() {
            applyClientRate(false);
        });
        rateInput.addEventListener('input', function() {
            rateInput.dataset.userEdited = 'true';
        });
        applyClientRate(false);
    }

    if (fixedFeeCheckbox) {
        const toggleFixedFee = function() {
            const isChecked = fixedFeeCheckbox.checked;
            setReadonlyState(rateInput, isChecked);
            setReadonlyState(hoursInput, isChecked);
        };
        fixedFeeCheckbox.addEventListener('change', toggleFixedFee);
        toggleFixedFee();
    }
});

// Client directory picker (browse under firm directory)
document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('client-dir-modal');
    const browseButton = document.getElementById('client-directory-browse');
    const directoryInput = document.getElementById('client_directory');
    const listEl = document.getElementById('client-dir-list');
    const currentEl = document.getElementById('client-dir-current');
    const backButton = document.getElementById('client-dir-back');
    const cancelButton = document.getElementById('client-dir-cancel');
    const selectButton = document.getElementById('client-dir-select');
    const nativePicker = document.getElementById('client-dir-native');
    const nativePickerButton = document.getElementById('client-dir-native-button');

    if (!modal || !browseButton || !directoryInput || !listEl || !currentEl || !backButton || !cancelButton || !selectButton) {
        return;
    }

    const baseDirectory = modal.dataset.firmDirectory || '';
    let currentPath = directoryInput.value.trim();
    if (currentPath.toLowerCase() === 'none') currentPath = '';
    let parentPath = '';

    const getSeparator = function() {
        return baseDirectory.includes('\\') ? '\\' : '/';
    };

    const displayPath = function(path) {
        if (!baseDirectory) {
            currentEl.textContent = 'Firm Directory not set.';
            return;
        }
        const separator = getSeparator();
        currentEl.textContent = baseDirectory + (path ? separator + path : '');
    };

    const renderList = function(directories) {
        listEl.innerHTML = '';
        if (!directories.length) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No subfolders found.';
            listEl.appendChild(empty);
            return;
        }

        directories.forEach(function(name) {
            const item = document.createElement('div');
            item.className = 'folder-item';
            item.textContent = name;
            item.addEventListener('click', function() {
                currentPath = currentPath ? currentPath + getSeparator() + name : name;
                loadDirectories(currentPath);
            });
            listEl.appendChild(item);
        });
    };

    const loadDirectories = function(path) {
        if (!baseDirectory) {
            displayPath('');
            renderList([]);
            backButton.disabled = true;
            return;
        }

        fetch('/api/list-directories?path=' + encodeURIComponent(path || ''))
            .then(function(response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            })
            .then(function(data) {
                if (data.error) {
                    currentEl.textContent = data.error;
                    renderList([]);
                    backButton.disabled = true;
                    if (nativePicker) nativePicker.click();
                    return;
                }

                currentPath = data.path || '';
                parentPath = data.parent || '';
                displayPath(currentPath);
                renderList(data.directories || []);
                backButton.disabled = !parentPath;
            })
            .catch(function() {
                currentEl.textContent = 'Unable to load folders.';
                renderList([]);
                backButton.disabled = true;
                if (nativePicker) nativePicker.click();
            });
    };

    browseButton.addEventListener('click', function() {
        modal.classList.add('active');
        if (directoryInput.value.trim() !== currentPath) {
            currentPath = directoryInput.value.trim();
        }
        if (currentPath && currentPath.includes(':')) {
            currentPath = '';
        }
        if (currentPath.toLowerCase() === 'none') currentPath = '';
        loadDirectories(currentPath);
    });

    backButton.addEventListener('click', function() {
        if (!parentPath) return;
        currentPath = parentPath;
        loadDirectories(currentPath);
    });

    cancelButton.addEventListener('click', function() {
        modal.classList.remove('active');
    });

    selectButton.addEventListener('click', function() {
        directoryInput.value = currentPath;
        modal.classList.remove('active');
    });

    if (nativePickerButton && nativePicker) {
        nativePickerButton.addEventListener('click', function() {
            nativePicker.click();
        });
    }

    if (nativePicker) {
        nativePicker.addEventListener('change', function() {
            if (!nativePicker.files || !nativePicker.files.length) return;
            const first = nativePicker.files[0];
            const relative = first.webkitRelativePath || '';
            if (!relative) return;
            const folder = relative.split(/[/\\]/)[0];
            directoryInput.value = folder;
            currentPath = folder;
            modal.classList.remove('active');
        });
    }
});

// File search: auto-fill directory from client selection
document.addEventListener('DOMContentLoaded', function() {
    const clientSelect = document.getElementById('search_client_id');
    const directoryInput = document.getElementById('search_directory');
    const searchingInText = document.getElementById('searching-in-text');

    if (!clientSelect || !directoryInput) return;

    const firmDirectory = directoryInput.dataset.firmDirectory || '';
    const separator = firmDirectory.includes('\\') ? '\\' : '/';

    const resolvePath = function(relativePath) {
        if (!relativePath) {
            return firmDirectory || '';
        }
        if (/^[a-zA-Z]:\\/.test(relativePath) || relativePath.startsWith('\\\\') || relativePath.startsWith('/')) {
            return relativePath;
        }
        if (!firmDirectory) return relativePath;

        const base = firmDirectory.replace(/[\\\/]+$/, '');
        const sub = relativePath.replace(/^[\\\/]+/, '');
        return base + separator + sub;
    };

    const applyDirectory = function() {
        const option = clientSelect.options[clientSelect.selectedIndex];
        const clientDir = option ? (option.dataset.clientDirectory || '') : '';
        directoryInput.value = resolvePath(clientDir);
        updateSearchingIn();
    };

    const updateSearchingIn = function() {
        if (!searchingInText) return;
        const value = directoryInput.value.trim();
        searchingInText.textContent = value || firmDirectory || '';
    };

    clientSelect.addEventListener('change', applyDirectory);
    directoryInput.addEventListener('input', updateSearchingIn);
    updateSearchingIn();
});

// Copy to clipboard buttons
document.addEventListener('click', function(e) {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.classList.contains('copy-btn')) return;

    const text = target.getAttribute('data-copy') || '';
    if (!text) return;

    const originalLabel = target.textContent || 'Copy';
    const setCopiedLabel = function() {
        target.textContent = 'Copied';
        setTimeout(function() {
            target.textContent = originalLabel;
        }, 1500);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(setCopiedLabel).catch(function() {
            setCopiedLabel();
        });
    } else {
        const temp = document.createElement('textarea');
        temp.value = text;
        temp.setAttribute('readonly', '');
        temp.style.position = 'absolute';
        temp.style.left = '-9999px';
        document.body.appendChild(temp);
        temp.select();
        document.execCommand('copy');
        document.body.removeChild(temp);
        setCopiedLabel();
    }
});

// File search: open a result file locally (or open its folder)
document.addEventListener('click', function(e) {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;

    const link = target.closest('.open-search-result');
    if (!link) return;

    e.preventDefault();
    const path = link.getAttribute('data-path') || '';
    if (!path) return;

    fetch('/api/open-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
    }).then(function() {
        // no-op
    }).catch(function(err) {
        console.warn('Unable to open file', err);
    });
});

// Settings: theme and font selection - instant preview and active state
document.addEventListener('DOMContentLoaded', function() {
    var themeOptions = document.querySelectorAll('.theme-option, .font-option');
    themeOptions.forEach(function(opt) {
        var input = opt.querySelector('input[type="radio"]');
        if (!input) return;
        input.addEventListener('change', function() {
            themeOptions.forEach(function(o) { o.classList.remove('active'); });
            opt.classList.add('active');
            if (input.name === 'theme') {
                document.body.setAttribute('data-theme', input.value);
            } else if (input.name === 'font') {
                document.body.setAttribute('data-font', input.value);
            }
        });
    });
});

// Settings: open the local data folder (database location)
document.addEventListener('click', function(e) {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;

    const link = target.closest('.open-data-folder');
    if (!link) return;

    e.preventDefault();
    fetch('/api/open-data-folder', { method: 'POST' })
        .then(function() {
            // no-op
        })
        .catch(function(err) {
            console.warn('Unable to open data folder', err);
        });
});
