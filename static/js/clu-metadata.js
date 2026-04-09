/**
 * CLU Metadata Operations  –  clu-metadata.js
 *
 * Shared metadata fetch for single files and directory batches.
 * Provides: CLU.searchMetadata, CLU.searchMetadataWithSelection,
 *           CLU.fetchDirectoryMetadata, CLU.fetchDirectoryMetadataWithVolume
 *
 * Depends on: clu-utils.js  (CLU.showToast, CLU.escapeHtml, etc.)
 *
 * External contract (set by page before calling):
 *   window._cluMetadata = {
 *     getLibraryId: function() { return null; },      // current library ID
 *     onMetadataFound: function(filePath, data) {},    // single-file success
 *     onMetadataError: function(filePath, error) {},   // single-file error (optional)
 *     onBatchComplete: function(dirPath, result) {}    // directory batch complete
 *   }
 *
 * DOM contracts:
 *   #comicVineVolumeModal, #gcdSeriesModal  (from partials/modal_metadata_select.html)
 */
(function () {
  'use strict';

  var CLU = window.CLU = window.CLU || {};

  // ── Contract helpers ──────────────────────────────────────────────────

  function _getContract() {
    return window._cluMetadata || {};
  }

  function _getLibraryId() {
    var c = _getContract();
    return typeof c.getLibraryId === 'function' ? c.getLibraryId() : null;
  }

  // ── Progress toast builder ────────────────────────────────────────────

  function _buildProgressToast(title, countText, detailText) {
    title = title || 'Fetching Metadata';
    countText = countText || '0/0';
    detailText = detailText || 'Starting...';

    var toast = document.createElement('div');
    toast.className = 'toast show position-fixed';
    toast.style.cssText = 'z-index: 1200; top: 60px; right: 1rem;';
    toast.innerHTML =
      '<div class="toast-header bg-primary text-white">' +
        '<strong class="me-auto">' + CLU.escapeHtml(title) + '</strong>' +
        '<small class="batch-progress-count">' + CLU.escapeHtml(countText) + '</small>' +
      '</div>' +
      '<div class="toast-body">' +
        '<div class="d-flex align-items-center">' +
          '<div class="spinner-border spinner-border-sm me-2" role="status">' +
            '<span class="visually-hidden">Loading...</span>' +
          '</div>' +
          '<span class="batch-progress-file text-truncate" style="max-width: 250px;">' + CLU.escapeHtml(detailText) + '</span>' +
        '</div>' +
      '</div>';
    document.body.appendChild(toast);
    return toast;
  }

  function _removeToast(toast) {
    if (toast && toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }

  // ── Batch result summary builder ──────────────────────────────────────

  function _buildSummary(result) {
    var parts = [];
    if (result.cvinfo_created) parts.push('cvinfo created');
    if (result.metron_id_added) parts.push('Metron ID added');
    if (result.cv_id_missing_warning) parts.push('ComicVine ID not available');
    if (result.processed > 0) parts.push(result.processed + ' file' + (result.processed !== 1 ? 's' : '') + ' updated');
    if (result.renamed > 0) parts.push(result.renamed + ' renamed');
    if (result.skipped > 0) parts.push(result.skipped + ' skipped');
    if (result.errors > 0) parts.push(result.errors + ' error' + (result.errors !== 1 ? 's' : ''));
    return parts.length > 0 ? parts.join(', ') : 'No changes made';
  }

  // ── SSE stream processor ──────────────────────────────────────────────

  function _processSSEStream(response, progressToast, onComplete) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var countEl = progressToast.querySelector('.batch-progress-count');
    var fileEl = progressToast.querySelector('.batch-progress-file');

    function read() {
      return reader.read().then(function (result) {
        if (result.done) return;

        buffer += decoder.decode(result.value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop();

        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line.indexOf('data: ') === 0) {
            try {
              var data = JSON.parse(line.slice(6));

              if (data.type === 'progress') {
                countEl.textContent = data.current + '/' + data.total;
                fileEl.textContent = data.file;
                fileEl.title = data.file;
              } else if (data.type === 'complete') {
                _removeToast(progressToast);
                if (typeof onComplete === 'function') {
                  onComplete(data.result);
                }
                return;
              }
            } catch (e) {
              console.error('Error parsing SSE data:', e);
            }
          }
        }

        return read();
      });
    }

    return read();
  }

  function _startSingleFileProgressWatcher(filePath, fileName, progressToast) {
    var label = (fileName || filePath || '').split('/').pop();
    var startedAfter = Date.now() / 1000;
    var countEl = progressToast.querySelector('.batch-progress-count');
    var fileEl = progressToast.querySelector('.batch-progress-file');
    var pollId = null;

    function updateToast(op) {
      if (!op) {
        return;
      }

      if (countEl) {
        countEl.textContent = op.total > 0 ? op.current + '/' + op.total : '';
      }
      if (fileEl) {
        fileEl.textContent = op.detail || 'Working...';
        fileEl.title = op.detail || '';
      }
    }

    function findOperation(operations) {
      var matches = (operations || []).filter(function (op) {
        return op.op_type === 'metadata' &&
          op.label === label &&
          op.started_at >= (startedAfter - 1);
      });

      matches.sort(function (a, b) {
        return (b.started_at || 0) - (a.started_at || 0);
      });

      return matches[0] || null;
    }

    function poll() {
      fetch('/api/operations?include_notifications=0')
        .then(function (response) { return response.json(); })
        .then(function (data) {
          updateToast(findOperation(data.operations));
        })
        .catch(function () {});
    }

    poll();
    pollId = window.setInterval(poll, 1000);

    return function stop() {
      if (pollId !== null) {
        window.clearInterval(pollId);
      }
    };
  }

  // ── Refine search modal (no-match fallback) ─────────────────────────

  function _showRefineSearchModal(data, filePath, fileName) {
    var termInput = document.getElementById('refineSearchTerm');
    var issueInput = document.getElementById('refineIssueNumber');
    var searchBtn = document.getElementById('refineSearchBtn');

    if (termInput && data.parsed_filename) {
      termInput.value = data.parsed_filename.series_name || '';
    }
    if (issueInput && data.parsed_filename) {
      issueInput.value = data.parsed_filename.issue_number || '';
    }

    // Replace button to remove old listeners
    if (searchBtn) {
      var newBtn = searchBtn.cloneNode(true);
      searchBtn.parentNode.replaceChild(newBtn, searchBtn);
      newBtn.addEventListener('click', function () {
        var refinedTerm = (document.getElementById('refineSearchTerm').value || '').trim();
        if (!refinedTerm) return;
        var modal = bootstrap.Modal.getInstance(document.getElementById('refineSearchModal'));
        if (modal) modal.hide();
        CLU.searchMetadata(filePath, fileName, refinedTerm);
      });
    }

    var modal = new bootstrap.Modal(document.getElementById('refineSearchModal'));
    modal.show();
  }

  // ── ComicVine volume modal: shared state & helpers ──────────────────

  var _cvVolumes = [];
  var _cvClickHandler = null;
  var _cvSortNameAsc = true;
  var _cvSortYearAsc = true;
  var _cvFilterFn = null;  // Override for custom filtering (e.g., GCD API language filter)
  var _cvSelectionMode = 'volume';
  var _cvSelectionContext = null;

  function _isCVIssueSelectionMode() {
    return _cvSelectionMode === 'issue';
  }

  function _compareCVIssueNumbers(a, b) {
    function parseIssueNumber(value) {
      var raw = (value || '').trim();
      var match = raw.match(/^(\d+)(?:\.(\d+))?$/);
      if (match) {
        return {
          type: 0,
          major: parseInt(match[1], 10),
          minor: parseInt(match[2] || '0', 10),
          raw: raw.toLowerCase()
        };
      }
      return { type: 1, major: 0, minor: 0, raw: raw.toLowerCase() };
    }

    var left = parseIssueNumber(a);
    var right = parseIssueNumber(b);
    if (left.type !== right.type) return left.type - right.type;
    if (left.major !== right.major) return left.major - right.major;
    if (left.minor !== right.minor) return left.minor - right.minor;
    return left.raw.localeCompare(right.raw);
  }

  function _getCVVolumeUrl(volume) {
    var url = volume && (volume.comicvine_url || volume.site_detail_url || volume.site_url);
    if (!url || typeof url !== 'string') return '';
    if (!/^https:\/\/comicvine\.gamespot\.com\//.test(url)) return '';
    return url;
  }

  function _renderCVVolumeList(volumes) {
    var volumeList = document.getElementById('cvVolumeList');
    volumeList.innerHTML = '';
    var isIssueMode = _isCVIssueSelectionMode();

    volumes.forEach(function (volume) {
      var volumeItem = document.createElement('div');
      volumeItem.className = 'list-group-item list-group-item-action d-flex align-items-start';
      volumeItem.style.cursor = 'pointer';

      var yearDisplay = isIssueMode ? (volume.cover_date || volume.year || 'Unknown') : (volume.start_year || 'Unknown');
      var issueCount = volume.count_of_issues === 0 || volume.count_of_issues ? volume.count_of_issues : 'Unknown';
      var comicVineUrl = isIssueMode ? '' : _getCVVolumeUrl(volume);
      var issueCountHtml = isIssueMode ? '' :
        '<small class="text-muted d-block mt-1">Issues: ' + CLU.escapeHtml(issueCount) + '</small>';
      var comicVineLinkHtml = comicVineUrl ?
        '<a href="' + CLU.escapeHtml(comicVineUrl) + '" class="cv-volume-link small d-block mt-1" target="_blank" rel="noopener noreferrer">ComicVine</a>' :
        '';
      var titleHtml = isIssueMode
        ? '<div class="fw-bold">#' + CLU.escapeHtml(volume.issue_number || '?') + ' ' + CLU.escapeHtml(volume.name || 'Untitled Issue') + '</div>'
        : '<div class="fw-bold">' + CLU.escapeHtml(volume.name) + '</div>';
      var metaHtml = isIssueMode
        ? '<small class="text-muted">Series: ' + CLU.escapeHtml(volume.volume_name || _cvSelectionContext && _cvSelectionContext.volume_name || 'Unknown') +
          '<br>Cover Date: ' + CLU.escapeHtml(volume.cover_date || 'Unknown') + '</small>'
        : '<small class="text-muted">Publisher: ' + CLU.escapeHtml(volume.publisher_name || 'Unknown') + '</small>';
      var descriptionPreview = volume.description ?
        '<small class="text-muted d-block mt-1">' + CLU.escapeHtml(volume.description) + '</small>' : '';

      var thumbnailHtml = volume.image_url ?
        '<img src="' + volume.image_url + '" class="img-thumbnail me-3" style="width: 80px; height: 120px; object-fit: cover;" alt="' + CLU.escapeHtml(volume.name || volume.issue_number || 'Issue') + ' cover">' :
        '<div class="me-3 d-flex align-items-center justify-content-center bg-secondary text-white" style="width: 80px; height: 120px; font-size: 10px;">No Cover</div>';

      var langBadge = volume.language ?
        '<span class="badge bg-info rounded-pill ms-1">' + CLU.escapeHtml((volume.language || '').toUpperCase()) + '</span>' : '';

      volumeItem.innerHTML =
        thumbnailHtml +
        '<div class="flex-grow-1 d-flex justify-content-between align-items-start">' +
          '<div class="me-2">' +
            titleHtml +
            metaHtml +
            descriptionPreview +
          '</div>' +
          '<div class="text-end flex-shrink-0">' +
            '<span class="badge bg-success rounded-pill">' + CLU.escapeHtml(yearDisplay) + '</span>' +
            langBadge +
            issueCountHtml +
            comicVineLinkHtml +
          '</div>' +
        '</div>';

      var volumeLink = volumeItem.querySelector('.cv-volume-link');
      if (volumeLink) {
        volumeLink.addEventListener('click', function (event) {
          event.stopPropagation();
        });
      }

      volumeItem.addEventListener('click', function () {
        _cvClickHandler(volume);
      });

      volumeList.appendChild(volumeItem);
    });
  }

  function _getFilteredVolumes() {
    var filterInput = document.getElementById('cvFilterInput');
    var filterText = (filterInput && filterInput.value || '').toLowerCase();
    if (!filterText) return _cvVolumes;
    return _cvVolumes.filter(function (v) {
      var searchable = _isCVIssueSelectionMode()
        ? [v.issue_number, v.name, v.cover_date, v.volume_name]
        : [v.name, v.publisher_name, v.start_year];
      return searchable.join(' ').toLowerCase().indexOf(filterText) !== -1;
    });
  }

  function _wireCVSortAndFilter() {
    var nameBtn = document.getElementById('cvSortByName');
    var yearBtn = document.getElementById('cvSortByYear');
    var filterInput = document.getElementById('cvFilterInput');

    // Reset UI state
    if (filterInput) filterInput.value = '';
    if (nameBtn) {
      nameBtn.className = 'btn btn-outline-secondary btn-sm';
      nameBtn.innerHTML = '<i class="bi bi-sort-alpha-down me-1"></i>' + (_isCVIssueSelectionMode() ? 'Issue' : 'Name');
    }
    if (yearBtn) {
      yearBtn.className = 'btn btn-outline-secondary btn-sm';
      yearBtn.innerHTML = '<i class="bi bi-sort-numeric-down me-1"></i>' + (_isCVIssueSelectionMode() ? 'Date' : 'Year');
    }

    // Reset sort direction
    _cvSortNameAsc = true;
    _cvSortYearAsc = true;

    // Clone buttons to remove old listeners
    if (nameBtn) {
      var newNameBtn = nameBtn.cloneNode(true);
      nameBtn.parentNode.replaceChild(newNameBtn, nameBtn);
      newNameBtn.addEventListener('click', function () {
        if (_isCVIssueSelectionMode()) {
          _cvVolumes.sort(function (a, b) {
            var result = _compareCVIssueNumbers(a.issue_number, b.issue_number);
            if (result === 0) {
              result = (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
            }
            return _cvSortNameAsc ? result : -result;
          });
        } else {
          var dir = _cvSortNameAsc ? 1 : -1;
          _cvVolumes.sort(function (a, b) {
            return dir * (a.name || '').toLowerCase().localeCompare((b.name || '').toLowerCase());
          });
        }
        newNameBtn.className = 'btn btn-secondary btn-sm';
        newNameBtn.innerHTML = '<i class="' + (_cvSortNameAsc ? 'bi bi-sort-alpha-down me-1' : 'bi bi-sort-alpha-up me-1') + '"></i>' + (_isCVIssueSelectionMode() ? 'Issue' : 'Name');
        _cvSortNameAsc = !_cvSortNameAsc;
        var otherBtn = document.getElementById('cvSortByYear');
        otherBtn.className = 'btn btn-outline-secondary btn-sm';
        otherBtn.innerHTML = '<i class="bi bi-sort-numeric-down me-1"></i>' + (_isCVIssueSelectionMode() ? 'Date' : 'Year');
        _cvSortYearAsc = true;
        _renderCVVolumeList((_cvFilterFn || _getFilteredVolumes)());
      });
    }
    if (yearBtn) {
      var newYearBtn = yearBtn.cloneNode(true);
      yearBtn.parentNode.replaceChild(newYearBtn, yearBtn);
      newYearBtn.addEventListener('click', function () {
        if (_isCVIssueSelectionMode()) {
          _cvVolumes.sort(function (a, b) {
            var aDate = Date.parse(a.cover_date || '') || 0;
            var bDate = Date.parse(b.cover_date || '') || 0;
            var result = aDate - bDate;
            if (result === 0) {
              result = _compareCVIssueNumbers(a.issue_number, b.issue_number);
            }
            return _cvSortYearAsc ? result : -result;
          });
        } else {
          var dir = _cvSortYearAsc ? 1 : -1;
          _cvVolumes.sort(function (a, b) {
            return dir * ((parseInt(a.start_year) || 0) - (parseInt(b.start_year) || 0));
          });
        }
        newYearBtn.className = 'btn btn-secondary btn-sm';
        newYearBtn.innerHTML = '<i class="' + (_cvSortYearAsc ? 'bi bi-sort-numeric-down me-1' : 'bi bi-sort-numeric-up me-1') + '"></i>' + (_isCVIssueSelectionMode() ? 'Date' : 'Year');
        _cvSortYearAsc = !_cvSortYearAsc;
        var otherBtn = document.getElementById('cvSortByName');
        otherBtn.className = 'btn btn-outline-secondary btn-sm';
        otherBtn.innerHTML = '<i class="bi bi-sort-alpha-down me-1"></i>' + (_isCVIssueSelectionMode() ? 'Issue' : 'Name');
        _cvSortNameAsc = true;
        _renderCVVolumeList((_cvFilterFn || _getFilteredVolumes)());
      });
    }
    if (filterInput) {
      var newFilterInput = filterInput.cloneNode(true);
      filterInput.parentNode.replaceChild(newFilterInput, filterInput);
      newFilterInput.addEventListener('input', function () {
        _renderCVVolumeList((_cvFilterFn || _getFilteredVolumes)());
      });
    }
  }

  // ── ComicVine volume modal (single-file context) ──────────────────────

  function _removeGCDApiLangFilter() {
    var el = document.getElementById('gcdApiLangFilter');
    if (el) el.parentNode.removeChild(el);
    _cvFilterFn = null;
    _gcdApiLangFilter = '';
  }

  function _showCVVolumeModal(data, filePath, fileName) {
    var providerType = data.provider || 'comicvine';
    var providerLabel = providerType === 'metron' ? 'Metron' : 'ComicVine';
    var isIssueMode = data.selection_type === 'issue';
    var resultLabel = isIssueMode ? 'Issue(s)' : (providerType === 'metron' ? 'Series' : 'Volume(s)');
    var prompt = document.getElementById('cvSelectionPrompt');

    _removeGCDApiLangFilter();
    _cvSelectionMode = isIssueMode ? 'issue' : 'volume';
    _cvSelectionContext = data.selected_match_context || null;
    // Show the inline refine row for single-file volume search only
    var refineRow = document.getElementById('cvRefineSearchRow');
    if (refineRow) refineRow.style.display = isIssueMode ? 'none' : '';

    var modalTitle = document.getElementById('comicVineVolumeModalLabel');
    if (modalTitle) {
      modalTitle.textContent = 'Select correct match (via ' + providerLabel + ') - ' + data.possible_matches.length + ' ' + resultLabel;
    }
    if (prompt) {
      if (isIssueMode) {
        var selectedVolumeName = _cvSelectionContext && _cvSelectionContext.volume_name;
        prompt.innerHTML = '<strong>Please select the correct issue' +
          (selectedVolumeName ? ' from ' + CLU.escapeHtml(selectedVolumeName) : '') +
          ':</strong>';
      } else {
        prompt.innerHTML = '<strong>Please select the correct ' + (providerType === 'metron' ? 'series' : 'volume') + ':</strong>';
      }
    }

    // Populate parsed info
    var cvSeries = document.getElementById('cvParsedSeries');
    var cvIssue = document.getElementById('cvParsedIssue');
    var cvYear = document.getElementById('cvParsedYear');
    if (cvSeries && data.parsed_filename) cvSeries.textContent = data.parsed_filename.series_name || '';
    if (cvIssue && data.parsed_filename) cvIssue.textContent = data.parsed_filename.issue_number || '';
    if (cvYear && data.parsed_filename) cvYear.textContent = data.parsed_filename.year || 'Unknown';

    // Store volumes and set click handler for single-file context
    _cvVolumes = data.possible_matches.slice();
    _cvClickHandler = function (volume) {
      var modal = bootstrap.Modal.getInstance(document.getElementById('comicVineVolumeModal'));
      modal.hide();

      var selectedMatch = { provider: providerType };
      if (isIssueMode) {
        selectedMatch.issue_id = volume.id;
        selectedMatch.volume_id = _cvSelectionContext && _cvSelectionContext.volume_id;
        selectedMatch.volume_name = _cvSelectionContext && _cvSelectionContext.volume_name;
        selectedMatch.publisher_name = _cvSelectionContext && _cvSelectionContext.publisher_name;
        selectedMatch.start_year = _cvSelectionContext && _cvSelectionContext.start_year;
      } else if (providerType === 'metron') {
        selectedMatch.series_id = volume.id;
      } else {
        selectedMatch.volume_id = volume.id;
        selectedMatch.volume_name = volume.name;
        selectedMatch.publisher_name = volume.publisher_name;
      }

      CLU.searchMetadataWithSelection(filePath, fileName, selectedMatch);
    };

    _wireCVSortAndFilter();
    _renderCVVolumeList(_cvVolumes);

    // Wire up inline refine search
    var cvRefineInput = document.getElementById('cvRefineSearchInput');
    var cvRefineBtn = document.getElementById('cvRefineSearchBtn');
    if (!isIssueMode && cvRefineInput && data.parsed_filename) {
      cvRefineInput.value = data.parsed_filename.series_name || '';
    }
    if (!isIssueMode && cvRefineBtn) {
      var newRefineBtn = cvRefineBtn.cloneNode(true);
      cvRefineBtn.parentNode.replaceChild(newRefineBtn, cvRefineBtn);
      newRefineBtn.addEventListener('click', function () {
        var refinedTerm = (document.getElementById('cvRefineSearchInput').value || '').trim();
        if (!refinedTerm) return;
        newRefineBtn.disabled = true;
        newRefineBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>Searching...';

        var requestBody = {
          file_path: filePath,
          file_name: fileName,
          search_term: refinedTerm,
          force_provider: providerType,
          force_manual_selection: true
        };
        var libraryId = _getLibraryId();
        if (libraryId) requestBody.library_id = libraryId;

        fetch('/api/search-metadata', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody)
        })
          .then(function (response) { return response.json(); })
          .then(function (newData) {
            newRefineBtn.disabled = false;
            newRefineBtn.innerHTML = '<i class="bi bi-search me-1"></i>Refine';

            if (newData.requires_selection && (newData.provider === 'comicvine' || newData.provider === 'metron')) {
              // Re-populate volume list in-place
              _showCVVolumeModal(newData, filePath, fileName);
            } else if (newData.success) {
              var modal = bootstrap.Modal.getInstance(document.getElementById('comicVineVolumeModal'));
              if (modal) modal.hide();
              CLU.showToast('Metadata Found', 'Metadata found via ' + newData.source, 'success');
              var contract = _getContract();
              if (typeof contract.onMetadataFound === 'function') {
                contract.onMetadataFound(filePath, newData);
              }
            } else {
              CLU.showToast('No Results', newData.error || 'No metadata found for refined search', 'warning');
            }
          })
          .catch(function (error) {
            newRefineBtn.disabled = false;
            newRefineBtn.innerHTML = '<i class="bi bi-search me-1"></i>Refine';
            CLU.showToast('Search Error', error.message || 'Failed to refine search', 'error');
          });
      });
    }

    var modal = new bootstrap.Modal(document.getElementById('comicVineVolumeModal'));
    modal.show();
  }

  // ── GCD API series modal (reuses ComicVine volume modal UI) ───────────

  // Track active GCD API language/country filter
  var _gcdApiLangFilter = '';

  function _showGCDApiVolumeModal(data, filePath, fileName) {
    _cvSelectionMode = 'volume';
    _cvSelectionContext = null;
    // Hide the inline refine row (not applicable for GCD API)
    var refineRow = document.getElementById('cvRefineSearchRow');
    if (refineRow) refineRow.style.display = 'none';

    // Populate parsed info
    var cvSeries = document.getElementById('cvParsedSeries');
    var cvIssue = document.getElementById('cvParsedIssue');
    var cvYear = document.getElementById('cvParsedYear');
    if (cvSeries && data.parsed_filename) cvSeries.textContent = data.parsed_filename.series_name || '';
    if (cvIssue && data.parsed_filename) cvIssue.textContent = data.parsed_filename.issue_number || '';
    if (cvYear && data.parsed_filename) cvYear.textContent = data.parsed_filename.year || 'Unknown';

    // Store volumes and set click handler
    _cvVolumes = data.possible_matches.slice();
    _gcdApiLangFilter = '';
    _cvClickHandler = function (volume) {
      var modal = bootstrap.Modal.getInstance(document.getElementById('comicVineVolumeModal'));
      if (modal) modal.hide();

      CLU.searchMetadataWithSelection(filePath, fileName, {
        provider: 'gcd_api',
        series_id: volume.id
      });
    };

    // Set custom filter function for GCD API (includes language filtering)
    _cvFilterFn = _getGCDApiFilteredVolumes;

    _wireCVSortAndFilter();

    // Build unique language/country values for the filter dropdown
    var langSet = {};
    _cvVolumes.forEach(function (v) {
      var lang = (v.language || '').toUpperCase();
      var country = (v.country || '').toUpperCase();
      var key = lang || country || '';
      if (key) langSet[key] = (langSet[key] || 0) + 1;
    });

    // Insert language filter dropdown after the existing filter controls
    var existingLangFilter = document.getElementById('gcdApiLangFilter');
    if (existingLangFilter) existingLangFilter.parentNode.removeChild(existingLangFilter);

    if (Object.keys(langSet).length > 1) {
      var filterContainer = document.getElementById('cvFilterInput');
      if (filterContainer && filterContainer.parentNode) {
        var selectEl = document.createElement('select');
        selectEl.id = 'gcdApiLangFilter';
        selectEl.className = 'form-select form-select-sm';
        selectEl.style.width = '120px';
        selectEl.innerHTML = '<option value="">All languages</option>';
        Object.keys(langSet).sort().forEach(function (key) {
          selectEl.innerHTML += '<option value="' + key + '">' + key + ' (' + langSet[key] + ')</option>';
        });
        filterContainer.parentNode.insertBefore(selectEl, filterContainer);

        selectEl.addEventListener('change', function () {
          _gcdApiLangFilter = selectEl.value;
          _renderCVVolumeList(_getGCDApiFilteredVolumes());
        });
      }
    }

    // Update title with count
    var modalTitle = document.getElementById('comicVineVolumeModalLabel');
    if (modalTitle) {
      modalTitle.textContent = 'Select correct match (via GCD API) — ' + data.possible_matches.length + ' Series';
    }

    _renderCVVolumeList(_cvVolumes);

    var modal = new bootstrap.Modal(document.getElementById('comicVineVolumeModal'));
    modal.show();
  }

  function _getGCDApiFilteredVolumes() {
    // Apply text filter
    var filtered = _getFilteredVolumes();
    // Apply language/country filter
    if (_gcdApiLangFilter) {
      var f = _gcdApiLangFilter.toLowerCase();
      filtered = filtered.filter(function (v) {
        return (v.language || '').toLowerCase() === f || (v.country || '').toLowerCase() === f;
      });
    }
    return filtered;
  }

  // ── GCD API start year prompt ─────────────────────────────────────────

  function _showGCDApiStartYearPrompt(data, filePath, fileName) {
    _cvSelectionMode = 'volume';
    _cvSelectionContext = null;
    var seriesName = (data.parsed_filename && data.parsed_filename.series_name) || 'Unknown';
    var message = data.message || ('No series found for "' + seriesName + '". Enter the year the series started.');

    // Reuse the ComicVine volume modal as a start year prompt
    var modalTitle = document.getElementById('comicVineVolumeModalLabel');
    if (modalTitle) {
      modalTitle.textContent = 'GCD API - Series Start Year Required';
    }

    var cvSeries = document.getElementById('cvParsedSeries');
    var cvIssue = document.getElementById('cvParsedIssue');
    var cvYear = document.getElementById('cvParsedYear');
    if (cvSeries) cvSeries.textContent = seriesName;
    if (cvIssue && data.parsed_filename) cvIssue.textContent = data.parsed_filename.issue_number || '';
    if (cvYear && data.parsed_filename) cvYear.textContent = data.parsed_filename.year || 'Unknown';

    // Hide refine search row
    var refineRow = document.getElementById('cvRefineSearchRow');
    if (refineRow) refineRow.style.display = 'none';

    var volumeList = document.getElementById('cvVolumeList');
    volumeList.innerHTML =
      '<div class="p-3">' +
        '<p class="text-muted">' + CLU.escapeHtml(message) + '</p>' +
        '<p class="text-muted small">The GCD API filters by the year a series <strong>started</strong>, ' +
          'not the issue publication year. For example, a 2026 issue may belong to a series that started in 2025.</p>' +
        '<div class="input-group mb-2">' +
          '<input type="number" id="gcdApiStartYearInput" class="form-control" placeholder="e.g. 2025" min="1900" max="2100">' +
          '<button class="btn btn-primary" type="button" id="gcdApiStartYearSearchBtn">' +
            '<i class="bi bi-search me-1"></i>Search' +
          '</button>' +
        '</div>' +
        '<button class="btn btn-outline-secondary btn-sm" type="button" id="gcdApiNoYearSearchBtn">' +
          'Search without year filter' +
        '</button>' +
      '</div>';

    // Wire up search with year
    var searchBtn = document.getElementById('gcdApiStartYearSearchBtn');
    var yearInput = document.getElementById('gcdApiStartYearInput');
    var noYearBtn = document.getElementById('gcdApiNoYearSearchBtn');

    function doSearch(startYear) {
      searchBtn.disabled = true;
      noYearBtn.disabled = true;
      searchBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Searching...';

      var requestBody = {
        file_path: filePath,
        file_name: fileName,
        search_term: seriesName,
        gcd_api_start_year: startYear || null
      };
      var libraryId = _getLibraryId();
      if (libraryId) requestBody.library_id = libraryId;

      fetch('/api/search-metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      })
        .then(function (response) { return response.json(); })
        .then(function (newData) {
          searchBtn.disabled = false;
          noYearBtn.disabled = false;
          searchBtn.innerHTML = '<i class="bi bi-search me-1"></i>Search';

          if (newData.requires_selection && newData.provider === 'gcd_api' && !newData.requires_start_year) {
            // Got results — close and show selection modal
            var cvModal = bootstrap.Modal.getInstance(document.getElementById('comicVineVolumeModal'));
            if (cvModal) cvModal.hide();
            _showGCDApiVolumeModal(newData, filePath, fileName);
          } else if (newData.success) {
            var cvModal = bootstrap.Modal.getInstance(document.getElementById('comicVineVolumeModal'));
            if (cvModal) cvModal.hide();
            CLU.showToast('Metadata Found', 'Metadata found via ' + newData.source, 'success');
            var contract = _getContract();
            if (typeof contract.onMetadataFound === 'function') {
              contract.onMetadataFound(filePath, newData);
            }
          } else if (newData.requires_start_year) {
            CLU.showToast('No Results', 'No series found with that year. Try a different year.', 'warning');
          } else {
            CLU.showToast('No Results', newData.error || 'No metadata found', 'warning');
          }
        })
        .catch(function (error) {
          searchBtn.disabled = false;
          noYearBtn.disabled = false;
          searchBtn.innerHTML = '<i class="bi bi-search me-1"></i>Search';
          CLU.showToast('Search Error', error.message || 'Search failed', 'error');
        });
    }

    searchBtn.addEventListener('click', function () {
      var yr = parseInt(yearInput.value, 10);
      if (!yr || yr < 1900 || yr > 2100) {
        CLU.showToast('Invalid Year', 'Please enter a valid year (1900-2100)', 'warning');
        return;
      }
      doSearch(yr);
    });

    yearInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        searchBtn.click();
      }
    });

    noYearBtn.addEventListener('click', function () {
      doSearch(null);
    });

    var modal = new bootstrap.Modal(document.getElementById('comicVineVolumeModal'));
    modal.show();

    // Focus the year input after modal is shown
    document.getElementById('comicVineVolumeModal').addEventListener('shown.bs.modal', function handler() {
      yearInput.focus();
      document.getElementById('comicVineVolumeModal').removeEventListener('shown.bs.modal', handler);
    });
  }

  // ── Provider series modal (batch/directory context) ───────────────────

  function _showBatchSeriesSelectionModal(data, dirPath, dirName) {
    _removeGCDApiLangFilter();
    _cvSelectionMode = 'volume';
    _cvSelectionContext = null;
    // Hide refine row for batch context
    var refineRow = document.getElementById('cvRefineSearchRow');
    if (refineRow) refineRow.style.display = 'none';

    // Populate parsed info
    var cvSeries = document.getElementById('cvParsedSeries');
    var cvIssue = document.getElementById('cvParsedIssue');
    var cvYear = document.getElementById('cvParsedYear');
    if (cvSeries) cvSeries.textContent = data.parsed_filename.series_name;
    if (cvIssue) cvIssue.textContent = data.parsed_filename.issue_number + ' files in folder';
    if (cvYear) cvYear.textContent = data.parsed_filename.year || 'Unknown';

    var modalTitle = document.getElementById('comicVineVolumeModalLabel');
    if (modalTitle) {
      var providerLabel = data.provider === 'metron' ? 'Metron' : 'ComicVine';
      modalTitle.textContent = 'Select Correct Series (' + providerLabel + ') - ' + data.possible_matches.length + ' result(s)';
    }

    // Store candidate matches and set click handler for batch context
    _cvVolumes = data.possible_matches.slice();
    _cvClickHandler = function (match) {
      var volumeList = document.getElementById('cvVolumeList');
      volumeList.querySelectorAll('.list-group-item').forEach(function (item) {
        item.classList.remove('active');
      });
      // Find and highlight the clicked item (by matching volume id)
      var items = volumeList.querySelectorAll('.list-group-item');
      items.forEach(function (item) { item.classList.remove('active'); });

      var modal = bootstrap.Modal.getInstance(document.getElementById('comicVineVolumeModal'));
      modal.hide();

      CLU.fetchDirectoryMetadataWithSelection(dirPath, dirName, {
        provider: data.provider || 'comicvine',
        id: match.id
      }, data._batchOptions || null);
    };

    _wireCVSortAndFilter();
    _renderCVVolumeList(_cvVolumes);

    var modal = new bootstrap.Modal(document.getElementById('comicVineVolumeModal'));
    modal.show();
  }

  // ── Public API: Single-file metadata search ───────────────────────────

  /**
   * Search metadata for a single file using library providers.
   * @param {string} filePath  Full path to the CBZ file
   * @param {string} fileName  Display name of the file
   */
  CLU.searchMetadata = function (filePath, fileName, searchTermOrOptions) {
    var libraryId = _getLibraryId();
    var contract = _getContract();
    var searchTerm = null;
    var forceProvider = null;
    var forceManualSelection = false;
    var progressToast = _buildProgressToast('Searching Metadata', '0/5', 'Starting request...');
    var stopProgressWatcher = _startSingleFileProgressWatcher(filePath, fileName, progressToast);

    if (typeof searchTermOrOptions === 'string') {
      searchTerm = searchTermOrOptions;
    } else if (searchTermOrOptions && typeof searchTermOrOptions === 'object') {
      searchTerm = searchTermOrOptions.searchTerm || null;
      forceProvider = searchTermOrOptions.forceProvider || null;
      forceManualSelection = !!searchTermOrOptions.forceManualSelection;
    }

    var requestBody = { file_path: filePath, file_name: fileName };
    if (libraryId) {
      requestBody.library_id = libraryId;
    }
    if (searchTerm) {
      requestBody.search_term = searchTerm;
    }
    if (forceProvider) {
      requestBody.force_provider = forceProvider;
    }
    if (forceManualSelection) {
      requestBody.force_manual_selection = true;
    }

    fetch('/api/search-metadata', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        stopProgressWatcher();
        _removeToast(progressToast);

        if (data.requires_selection) {
          if (data.provider === 'comicvine' || data.provider === 'metron') {
            _showCVVolumeModal(data, filePath, fileName);
          } else if (data.provider === 'gcd') {
            // Use page-level GCD modal if available, otherwise show info
            if (typeof showGCDSeriesSelectionModal === 'function') {
              showGCDSeriesSelectionModal(data, filePath, fileName);
              window._cascadeGCDSelection = { filePath: filePath, fileName: fileName, libraryId: libraryId };
            } else {
              CLU.showToast('GCD Selection', 'GCD series selection requires the Files page', 'warning');
            }
          } else if (data.provider === 'gcd_api') {
            if (data.requires_start_year) {
              _showGCDApiStartYearPrompt(data, filePath, fileName);
            } else {
              _showGCDApiVolumeModal(data, filePath, fileName);
            }
          } else if (['mangadex', 'mangaupdates', 'anilist'].indexOf(data.provider) !== -1) {
            if (typeof showMangaSeriesSelectionModal === 'function') {
              showMangaSeriesSelectionModal(data, filePath, fileName, libraryId);
            } else {
              CLU.showToast('Series Selection', 'Series selection requires the Files page', 'warning');
            }
          }
          return;
        }

        if (data.success) {
          CLU.showToast('Metadata Found', 'Metadata found via ' + data.source, 'success');

          if (typeof contract.onMetadataFound === 'function') {
            contract.onMetadataFound(filePath, data);
          }
          return;
        }

        if (data.parsed_filename) {
          _showRefineSearchModal(data, filePath, fileName);
        } else {
          CLU.showToast('No Metadata', data.error || 'No metadata found from any provider', 'warning');
        }
        if (typeof contract.onMetadataError === 'function') {
          contract.onMetadataError(filePath, data.error || 'No metadata found');
        }
      })
      .catch(function (error) {
        stopProgressWatcher();
        _removeToast(progressToast);
        console.error('Metadata search error:', error);
        CLU.showToast('Metadata Error', error.message || 'Failed to search metadata', 'error');
        if (typeof contract.onMetadataError === 'function') {
          contract.onMetadataError(filePath, error.message);
        }
      });
  };

  CLU.forceSearchMetadata = function (filePath, fileName, forceProvider) {
    return CLU.searchMetadata(filePath, fileName, {
      forceProvider: forceProvider,
      forceManualSelection: true
    });
  };

  // ── Public API: Single-file with user selection ───────────────────────

  /**
   * Follow-up search after user picks a volume/series.
   * @param {string} filePath
   * @param {string} fileName
   * @param {Object} selectedMatch  { provider, volume_id, publisher_name }
   */
  CLU.searchMetadataWithSelection = function (filePath, fileName, selectedMatch) {
    var libraryId = _getLibraryId();
    var contract = _getContract();
    var providerLabel = selectedMatch.provider || 'provider';
    var progressToast = _buildProgressToast('Fetching Metadata', '0/5', 'Waiting for ' + CLU.escapeHtml(providerLabel) + '...');
    var stopProgressWatcher = _startSingleFileProgressWatcher(filePath, fileName, progressToast);

    var requestBody = {
      file_path: filePath,
      file_name: fileName,
      selected_match: selectedMatch
    };
    if (libraryId) {
      requestBody.library_id = libraryId;
    }

    fetch('/api/search-metadata', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        stopProgressWatcher();
        _removeToast(progressToast);

        if (data.requires_selection && (data.provider === 'comicvine' || data.provider === 'metron')) {
          _showCVVolumeModal(data, filePath, fileName);
        } else if (data.success) {
          CLU.showToast('Metadata Found', 'Metadata found via ' + data.source, 'success');

          if (typeof contract.onMetadataFound === 'function') {
            contract.onMetadataFound(filePath, data);
          }
        } else {
          CLU.showToast('Metadata Error', data.error || 'No metadata found for selection', 'error');
          if (typeof contract.onMetadataError === 'function') {
            contract.onMetadataError(filePath, data.error);
          }
        }
      })
      .catch(function (error) {
        stopProgressWatcher();
        _removeToast(progressToast);
        console.error('Metadata selection error:', error);
        CLU.showToast('Metadata Error', error.message || 'Failed to fetch metadata', 'error');
        if (typeof contract.onMetadataError === 'function') {
          contract.onMetadataError(filePath, error.message);
        }
      });
  };

  // ── Public API: Directory batch metadata ──────────────────────────────

  function _requestBatchMetadata(dirPath, dirName, options, selection) {
    var libraryId = _getLibraryId();
    var contract = _getContract();
    var progressToast = _buildProgressToast();

    var requestBody = { directory: dirPath };
    if (selection && selection.provider === 'comicvine' && selection.id !== null && typeof selection.id !== 'undefined') {
      requestBody.volume_id = selection.id;
    }
    if (selection && selection.provider === 'metron' && selection.id !== null && typeof selection.id !== 'undefined') {
      requestBody.series_id = selection.id;
    }
    if (libraryId) {
      requestBody.library_id = libraryId;
    }
    if (options && options.forceManualSelection) {
      requestBody.force_manual_selection = true;
    }
    if (options && options.forceProvider) {
      requestBody.force_provider = options.forceProvider;
    }
    if (options && options.overwriteExistingMetadata) {
      requestBody.overwrite_existing_metadata = true;
    }

    fetch('/api/batch-metadata', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    })
      .then(function (response) {
        var contentType = response.headers.get('content-type');
        if (contentType && contentType.indexOf('application/json') !== -1) {
          return response.json().then(function (data) {
            if (data.requires_selection) {
              _removeToast(progressToast);
              data._batchOptions = options || null;
              _showBatchSeriesSelectionModal(data, dirPath, dirName);
              return;
            }
            if (data.error) {
              throw new Error(data.error);
            }
          });
        }

        return _processSSEStream(response, progressToast, function (result) {
          var summary = _buildSummary(result);
          var toastType = result.errors > 0 ? 'warning' : (result.processed > 0 || result.cvinfo_created ? 'success' : 'info');
          CLU.showToast('Metadata Fetch Complete', summary, toastType);

          if (typeof contract.onBatchComplete === 'function') {
            contract.onBatchComplete(dirPath, result);
          }
        });
      })
      .catch(function (error) {
        _removeToast(progressToast);
        CLU.showToast('Metadata Error', 'Error fetching metadata: ' + error.message, 'error');
      });
  }

  /**
   * Fetch metadata for all files in a directory via SSE streaming.
   * @param {string} dirPath   Full path to the directory
   * @param {string} dirName   Display name of the directory
   */
  CLU.fetchDirectoryMetadata = function (dirPath, dirName) {
    _requestBatchMetadata(dirPath, dirName, null, null);
  };

  CLU.forceFetchDirectoryMetadataViaComicVine = function (dirPath, dirName) {
    _requestBatchMetadata(dirPath, dirName, {
      forceManualSelection: true,
      forceProvider: 'comicvine',
      overwriteExistingMetadata: true
    }, null);
  };

  CLU.forceFetchDirectoryMetadataViaMetron = function (dirPath, dirName) {
    _requestBatchMetadata(dirPath, dirName, {
      forceManualSelection: true,
      forceProvider: 'metron',
      overwriteExistingMetadata: true
    }, null);
  };

  // ── Public API: Directory batch with pre-selected provider match ──────

  /**
   * Fetch metadata for all files in a directory with a pre-selected volume.
   * @param {string} dirPath
   * @param {string} dirName
   * @param {Object} selection  { provider, id }
   */
  CLU.fetchDirectoryMetadataWithSelection = function (dirPath, dirName, selection, options) {
    _requestBatchMetadata(dirPath, dirName, options || null, selection || null);
  };

  CLU.fetchDirectoryMetadataWithVolume = function (dirPath, dirName, volumeId, options) {
    _requestBatchMetadata(dirPath, dirName, options || null, {
      provider: 'comicvine',
      id: volumeId
    });
  };

})();
