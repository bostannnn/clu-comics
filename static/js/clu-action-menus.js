(function () {
  'use strict';

  var CLU = window.CLU = window.CLU || {};

  function _isFn(fn) {
    return typeof fn === 'function';
  }

  function _buildMenuItem(definition) {
    var li = document.createElement('li');

    if (definition.divider) {
      var divider = document.createElement('hr');
      divider.className = 'dropdown-divider';
      li.appendChild(divider);
      return li;
    }

    var link = document.createElement('a');
    link.className = definition.className || 'dropdown-item';
    link.href = '#';

    var labelHtml = definition.icon
      ? '<i class="' + definition.icon + ' me-2"></i>' + CLU.escapeHtml(definition.label)
      : CLU.escapeHtml(definition.label);

    link.innerHTML = labelHtml;
    link.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      definition.onClick(event);
    });

    li.appendChild(link);
    return li;
  }

  function _pushAction(target, label, icon, onClick, className) {
    if (!_isFn(onClick)) {
      return;
    }

    target.push({
      label: label,
      icon: icon,
      onClick: onClick,
      className: className || 'dropdown-item'
    });
  }

  function _appendItems(target, items) {
    if (!Array.isArray(items)) {
      return;
    }

    items.forEach(function (item) {
      if (!item || item.hidden || !_isFn(item.onClick)) {
        return;
      }
      target.push(item);
    });
  }

  function _renderMenuSections(menuElement, sections) {
    if (!menuElement) {
      return;
    }

    menuElement.innerHTML = '';
    var nonEmptySections = sections.filter(function (section) {
      return Array.isArray(section) && section.length > 0;
    });

    nonEmptySections.forEach(function (section, index) {
      section.forEach(function (item) {
        menuElement.appendChild(_buildMenuItem(item));
      });

      if (index < nonEmptySections.length - 1) {
        menuElement.appendChild(_buildMenuItem({ divider: true }));
      }
    });
  }

  CLU.populateIssueActionMenu = function (menuElement, context) {
    context = context || {};

    var fileOps = [];
    _pushAction(fileOps, 'Crop Cover', 'bi bi-scissors', context.onCropCover);
    _pushAction(fileOps, 'Remove 1st Image', 'bi bi-file-minus', context.onRemoveFirstImage);
    _pushAction(fileOps, 'Edit File', 'bi bi-pencil-square', context.onEditFile);
    _pushAction(fileOps, 'Apply Rename Pattern', 'bi bi-input-cursor-text', context.onApplyRenamePattern);
    _pushAction(fileOps, 'Apply Folder + Rename Pattern', 'bi bi-folder-symlink', context.onApplyFolderRenamePattern);
    _pushAction(fileOps, 'Rebuild', 'bi bi-arrow-repeat', context.onRebuild);
    _pushAction(fileOps, 'Enhance', 'bi bi-stars', context.onEnhance);
    _appendItems(fileOps, context.extraFileOps);

    var metadataOps = [];
    _pushAction(metadataOps, 'Fetch Metadata', 'bi bi-cloud-download', context.onFetchMetadata);
    _pushAction(metadataOps, 'Force ComicVine', 'bi bi-cloud-check', context.onForceComicVine);
    _pushAction(metadataOps, 'Force Metron', 'bi bi-cloud-check', context.onForceMetron);
    _appendItems(metadataOps, context.extraMetadataActions);

    var readingOps = [];
    _pushAction(readingOps, context.readDateLabel || 'Set Read Date', 'bi bi-calendar-check', context.onSetReadDate);
    _pushAction(readingOps, 'Mark as Unread', 'bi bi-book', context.onMarkUnread);
    _pushAction(readingOps, 'Hide from History', 'bi bi-eye-slash', context.onHideFromHistory);
    _appendItems(readingOps, context.extraReadingActions);
    _pushAction(readingOps, 'Add to Reading List', 'bi bi-journal-plus', context.onAddToReadingList);
    _appendItems(readingOps, context.extraPostReadingActions);

    var destructiveOps = [];
    _pushAction(destructiveOps, 'Delete', 'bi bi-trash', context.onDelete, 'dropdown-item text-danger');

    _renderMenuSections(menuElement, [fileOps, metadataOps, readingOps, destructiveOps]);
  };

  CLU.populateFolderActionMenu = function (menuElement, context) {
    context = context || {};

    var processingOps = [];
    _pushAction(processingOps, 'Generate Thumbnail', 'bi bi-image', context.onGenerateThumbnail);
    _pushAction(processingOps, 'Generate All Missing Thumbnails', 'bi bi-images', context.onGenerateAllMissingThumbnails);
    _pushAction(processingOps, 'Convert CBR→CBZ', 'bi bi-arrow-repeat', context.onConvertCbrToCbz);
    _pushAction(processingOps, 'Rebuild All Files', 'bi bi-hammer', context.onRebuildAllFiles);
    _pushAction(processingOps, 'PDFs→CBZ', 'bi bi-file-earmark-pdf', context.onConvertPdfToCbz);
    _pushAction(processingOps, 'Enhance Images', 'bi bi-stars', context.onEnhanceImages);
    _appendItems(processingOps, context.extraProcessingActions);

    var metadataOps = [];
    _pushAction(metadataOps, 'Fetch All Metadata', 'bi bi-cloud-download', context.onFetchAllMetadata);
    _pushAction(metadataOps, 'Force Fetch via ComicVine', 'bi bi-cloud-check', context.onForceComicVine);
    _pushAction(metadataOps, 'Force Fetch via Metron', 'bi bi-cloud-check', context.onForceMetron);
    _appendItems(metadataOps, context.extraMetadataActions);

    var maintenanceOps = [];
    _pushAction(maintenanceOps, 'Scan Files', 'bi bi-arrow-clockwise', context.onScanFiles);
    _pushAction(maintenanceOps, 'Missing File Check', 'bi bi-file-earmark-text', context.onMissingFileCheck);
    _pushAction(maintenanceOps, 'Update XML', 'bi bi-filetype-xml', context.onUpdateXml);
    _pushAction(maintenanceOps, 'Remove All XML', 'bi bi-eraser', context.onRemoveAllXml, 'dropdown-item text-danger');
    _appendItems(maintenanceOps, context.extraMaintenanceActions);

    var destructiveOps = [];
    _pushAction(destructiveOps, 'Delete', 'bi bi-trash', context.onDelete, 'dropdown-item text-danger');

    _renderMenuSections(menuElement, [processingOps, metadataOps, maintenanceOps, destructiveOps]);
  };

  CLU.applyRenamePatternToFile = function (filePath, hooks) {
    hooks = hooks || {};

    var loadingToast = document.createElement('div');
    loadingToast.className = 'toast show position-fixed top-0 end-0 m-3';
    loadingToast.style.zIndex = '1200';
    loadingToast.innerHTML =
      '<div class="toast-header bg-primary text-white">' +
        '<strong class="me-auto">Applying Rename Pattern</strong>' +
      '</div>' +
      '<div class="toast-body">' +
        '<div class="d-flex align-items-center">' +
          '<div class="spinner-border spinner-border-sm me-2" role="status">' +
            '<span class="visually-hidden">Loading...</span>' +
          '</div>' +
          'Renaming file...' +
        '</div>' +
      '</div>';
    document.body.appendChild(loadingToast);

    fetch('/apply-rename-pattern', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ path: filePath })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (document.body.contains(loadingToast)) {
          document.body.removeChild(loadingToast);
        }

        if (!result.ok) {
          throw new Error(result.data.error || 'Failed to apply rename pattern');
        }

        if (result.data.renamed) {
          CLU.showToast('File Renamed', 'Successfully renamed to: ' + result.data.new_name, 'success');
        } else {
          CLU.showToast(
            'No Rename Needed',
            result.data.message || 'File already matches the custom rename pattern.',
            'info'
          );
        }

        if (_isFn(hooks.onSuccess)) {
          hooks.onSuccess(result.data);
        }
      })
      .catch(function (error) {
        if (document.body.contains(loadingToast)) {
          document.body.removeChild(loadingToast);
        }
        if (_isFn(hooks.onError)) {
          hooks.onError(error);
        } else {
          CLU.showToast('Rename Error', error.message, 'error');
        }
      });
  };

  CLU.applyFolderRenamePatternToFile = function (filePath, hooks) {
    hooks = hooks || {};

    var loadingToast = document.createElement('div');
    loadingToast.className = 'toast show position-fixed top-0 end-0 m-3';
    loadingToast.style.zIndex = '1200';
    loadingToast.innerHTML =
      '<div class="toast-header bg-primary text-white">' +
        '<strong class="me-auto">Applying Folder + Rename Pattern</strong>' +
      '</div>' +
      '<div class="toast-body">' +
        '<div class="d-flex align-items-center">' +
          '<div class="spinner-border spinner-border-sm me-2" role="status">' +
            '<span class="visually-hidden">Loading...</span>' +
          '</div>' +
          'Moving and renaming file...' +
        '</div>' +
      '</div>';
    document.body.appendChild(loadingToast);

    fetch('/apply-folder-rename-pattern', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ path: filePath })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (document.body.contains(loadingToast)) {
          document.body.removeChild(loadingToast);
        }

        if (!result.ok) {
          throw new Error(result.data.error || 'Failed to apply folder and rename pattern');
        }

        if (result.data.updated) {
          CLU.showToast('File Moved', 'Successfully moved and renamed to: ' + result.data.new_name, 'success');
        } else {
          CLU.showToast(
            'No Move Needed',
            result.data.message || 'File already matches the custom folder and rename patterns.',
            'info'
          );
        }

        if (_isFn(hooks.onSuccess)) {
          hooks.onSuccess(result.data);
        }
      })
      .catch(function (error) {
        if (document.body.contains(loadingToast)) {
          document.body.removeChild(loadingToast);
        }
        if (_isFn(hooks.onError)) {
          hooks.onError(error);
        } else {
          CLU.showToast('Move Error', error.message, 'error');
        }
      });
  };

  CLU.applyFolderRenamePatternToDirectory = function (directoryPath, hooks) {
    hooks = hooks || {};

    var loadingToast = document.createElement('div');
    loadingToast.className = 'toast show position-fixed top-0 end-0 m-3';
    loadingToast.style.zIndex = '1200';
    loadingToast.innerHTML =
      '<div class="toast-header bg-primary text-white">' +
        '<strong class="me-auto">Applying Folder + Rename Pattern</strong>' +
      '</div>' +
      '<div class="toast-body">' +
        '<div class="d-flex align-items-center">' +
          '<div class="spinner-border spinner-border-sm me-2" role="status">' +
            '<span class="visually-hidden">Loading...</span>' +
          '</div>' +
          'Processing files in folder...' +
        '</div>' +
      '</div>';
    document.body.appendChild(loadingToast);

    fetch('/apply-folder-rename-pattern', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ path: directoryPath })
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, data: data };
        });
      })
      .then(function (result) {
        if (document.body.contains(loadingToast)) {
          document.body.removeChild(loadingToast);
        }

        if (!result.ok) {
          throw new Error(result.data.error || 'Failed to apply folder and rename pattern');
        }

        CLU.showToast(
          result.data.updated ? 'Folder Processed' : 'No Changes Applied',
          result.data.message || 'Finished processing folder.',
          result.data.failed_count > 0 ? 'warning' : (result.data.updated ? 'success' : 'info')
        );

        if (_isFn(hooks.onSuccess)) {
          hooks.onSuccess(result.data);
        }
      })
      .catch(function (error) {
        if (document.body.contains(loadingToast)) {
          document.body.removeChild(loadingToast);
        }
        if (_isFn(hooks.onError)) {
          hooks.onError(error);
        } else {
          CLU.showToast('Move Error', error.message, 'error');
        }
      });
  };
})();
