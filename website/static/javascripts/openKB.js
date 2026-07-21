function openKbTranslatedText(attributeName, fallbackText){
    var source = document.querySelector('[data-openkb-search-history-title]');
    if(!source){
        return fallbackText;
    }
    var value = source.getAttribute(attributeName);
    return value || fallbackText;
}

$(document).ready(function(){

    // Search history dropdown for normal search bars only.
    // This does NOT attach to the OpenKB AI chatbox.
    // Stored in browser sessionStorage, so it only lasts for the current browser session.
    (function(){
        var MAX_HISTORY_ITEMS = 5;
        var MAX_VISIBLE_ITEMS = 5;

        function storageAvailable(){
            try{
                var testKey = '__djopenkb_history_test__';
                window.sessionStorage.setItem(testKey, '1');
                window.sessionStorage.removeItem(testKey);
                return true;
            }catch(error){
                return false;
            }
        }

        if(!storageAvailable()){
            return;
        }

        function getHistory(storageKey){
            try{
                var items = JSON.parse(window.sessionStorage.getItem(storageKey) || '[]');
                if(!Array.isArray(items)){
                    return [];
                }
                return items.filter(function(item){
                    return typeof item === 'string' && item.trim() !== '';
                });
            }catch(error){
                return [];
            }
        }

        function setHistory(storageKey, items){
            window.sessionStorage.setItem(storageKey, JSON.stringify(items.slice(0, MAX_HISTORY_ITEMS)));
        }

        function addHistoryItem(storageKey, value){
            value = $.trim(value || '');
            if(value === ''){
                return;
            }

            var lowerValue = value.toLowerCase();
            var items = getHistory(storageKey).filter(function(item){
                return item.toLowerCase() !== lowerValue;
            });

            items.unshift(value);
            setHistory(storageKey, items);
        }

        function removeHistoryItem(storageKey, value){
            var lowerValue = (value || '').toLowerCase();
            var items = getHistory(storageKey).filter(function(item){
                return item.toLowerCase() !== lowerValue;
            });
            setHistory(storageKey, items);
        }

        function injectSearchHistoryStyles(){
            if($('#djopenkb-search-history-style').length){
                return;
            }

            $('head').append(
                '<style id="djopenkb-search-history-style">' +
                '.search-history-container{position:relative;}' +
                '.search-history-dropdown{position:absolute;z-index:10050;box-sizing:border-box;background:#fff;border:1px solid #dce4ec;border-radius:6px;box-shadow:0 6px 18px rgba(0,0,0,.16);max-height:260px;overflow-x:hidden;overflow-y:auto;text-align:left;}' +
                '.search-history-dropdown.hidden{display:none;}' +
                '.search-history-title{padding:7px 11px;color:#7b8a8b;font-size:12px;font-weight:700;text-transform:uppercase;white-space:nowrap;border-bottom:1px solid #eef2f4;background:#f8fafb;}' +
                '.search-history-item{display:flex;align-items:center;column-gap:8px;width:100%;min-width:0;background:#fff;padding:8px 9px;color:#2c3e50;text-align:left;cursor:pointer;}' +
                '.search-history-item:hover,.search-history-item:focus{background:#f4f8fb;outline:none;}' +
                '.search-history-term{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
                '.search-history-icon{flex:0 0 auto;color:#95a5a6;}' +
                '.search-history-remove{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border:0;border-radius:3px;background:transparent;color:#95a5a6;padding:0;line-height:1;}' +
                '.search-history-remove:hover,.search-history-remove:focus{background:#fff;color:#c0392b;outline:none;}' +
                '</style>'
            );
        }

        function setupHistoryDropdown($input, options){
            if(!$input.length || $input.data('search-history-ready')){
                return;
            }

            $input.data('search-history-ready', true);
            $input.attr('autocomplete', 'off');

            var storageKey = options.storageKey;
            var $searchHistoryI18n = $('[data-openkb-search-history-title]').first();
            var title = options.title || $searchHistoryI18n.attr('data-openkb-search-history-title') || openKbTranslatedText('data-openkb-search-history-title', 'Search history');
            var removeLabel = options.removeLabel || $searchHistoryI18n.attr('data-openkb-search-history-remove-label') || openKbTranslatedText('data-openkb-search-history-remove-label', 'Remove search history item');

            // Keep the dropdown outside Bootstrap's .input-group. The input-group
            // uses table-style layout and absolutely positioned children can collapse to
            // the search button width instead of following the full search field.
            var $anchor = $input.closest('.input-group');
            if(!$anchor.length){
                $anchor = $input;
            }

            var $container = $input.closest('.search_bar');
            if(!$container.length){
                $container = $anchor.parent();
            }
            $container.addClass('search-history-container');

            var $dropdown = $('<div class="search-history-dropdown hidden" role="listbox"></div>');
            $container.append($dropdown);

            function positionDropdown(){
                var containerOffset = $container.offset();
                var anchorOffset = $anchor.offset();

                if(!containerOffset || !anchorOffset){
                    return;
                }

                $dropdown.css({
                    left: anchorOffset.left - containerOffset.left,
                    top: (anchorOffset.top - containerOffset.top) + $anchor.outerHeight() + 4,
                    width: $anchor.outerWidth()
                });
            }

            function hideDropdown(){
                $dropdown.addClass('hidden');
            }

            function renderDropdown(showFullHistory){
                var history = getHistory(storageKey);
                var currentValue = $.trim($input.val() || '').toLowerCase();

                // Clicking/focusing the search field should always let the user reopen
                // the complete recent-search history, matching the original behaviour.
                // While the user is actively typing, however, the newer article
                // title/keyword suggestions take precedence once there is enough text.
                if(!showFullHistory && $input.is('#frm_search') && currentValue.length >= 2){
                    hideDropdown();
                    return;
                }

                if(!showFullHistory && currentValue){
                    history = history.filter(function(item){
                        return item.toLowerCase().indexOf(currentValue) !== -1;
                    });
                }

                history = history.slice(0, MAX_VISIBLE_ITEMS);
                $dropdown.empty();

                if(history.length === 0){
                    hideDropdown();
                    return;
                }

                $dropdown.append($('<div class="search-history-title"></div>').text(title));

                history.forEach(function(item){
                    // Use a focusable div instead of nesting a remove <button> inside
                    // another <button>, which is invalid HTML and is rendered
                    // inconsistently by browsers.
                    var $row = $('<div class="search-history-item" role="option" tabindex="0"></div>');
                    var $icon = $('<i class="fa fa-history search-history-icon" aria-hidden="true"></i>');
                    var $term = $('<span class="search-history-term"></span>').text(item);
                    var $remove = $('<button type="button" class="search-history-remove"><i class="fa fa-times" aria-hidden="true"></i></button>').attr('aria-label', removeLabel);

                    $row.append($icon).append($term).append($remove);

                    function chooseHistoryItem(){
                        $input.val(item);

                        // Notify the newer live title/keyword suggestion handler that
                        // the input value changed, then return focus to the search bar.
                        if($input[0] && typeof Event === 'function'){
                            $input[0].dispatchEvent(new Event('input', {bubbles: true}));
                        }

                        $input.focus();
                        hideDropdown();
                    }

                    $row.on('mousedown', function(event){
                        if(!$(event.target).closest('.search-history-remove').length){
                            event.preventDefault();
                        }
                    });

                    $row.on('click', function(event){
                        if($(event.target).closest('.search-history-remove').length){
                            return;
                        }
                        chooseHistoryItem();
                    });

                    $row.on('keydown', function(event){
                        if(event.key === 'Enter' || event.key === ' '){
                            event.preventDefault();
                            chooseHistoryItem();
                        }
                    });

                    $remove.on('mousedown click', function(event){
                        event.preventDefault();
                        event.stopPropagation();

                        if(event.type === 'click'){
                            removeHistoryItem(storageKey, item);
                            renderDropdown();
                        }
                    });

                    $dropdown.append($row);
                });

                positionDropdown();
                $dropdown.removeClass('hidden');
            }

            $input.on('focus click', function(){
                // History is intentionally shown on click/focus, even when the field
                // still contains a previous query. Hide the live article-suggestion
                // dropdown first so only one dropdown is visible at a time.
                if($input.is('#frm_search')){
                    $('#searchResult').addClass('hidden');
                }
                renderDropdown(true);
            });

            $input.on('input', function(){
                // Once the user edits the query, switch back to filtered history for a
                // short value, or let the normal title/keyword suggestions take over.
                renderDropdown(false);
            });

            $input.on('keydown', function(event){
                if(event.key === 'Escape'){
                    hideDropdown();
                }
            });

            $input.closest('form').on('submit', function(){
                addHistoryItem(storageKey, $input.val());
            });

            $(window).on('resize', positionDropdown);

            $(document).on('mousedown', function(event){
                if(!$(event.target).closest($container).length){
                    hideDropdown();
                }
            });
        }

        injectSearchHistoryStyles();

        var seenInputs = [];
        $('input[type="text"][name="q"], #frm_search').each(function(){
            var inputId = this.id || '';

            // Individual search fields can explicitly opt out of search history.
            if($(this).attr('data-search-history') === 'off'){
                return;
            }

            // Do not enable history for the OpenKB AI question/chatbox input.
            if(inputId === 'openkbAiQuestion' || $(this).closest('#openkbAiBox, .openkb-ai-box, .ai-chatbox, .chatbox').length){
                return;
            }

            // The modern home/internal search uses the existing #searchResult dropdown
            // for both recent-search history and live article suggestions. Attaching
            // this older standalone history dropdown as well would create two
            // competing dropdowns for the same input.
            if(inputId === 'frm_search' && $('#searchResult[data-suggestions-url]').length){
                return;
            }

            if(seenInputs.indexOf(this) === -1){
                seenInputs.push(this);
                setupHistoryDropdown($(this), {
                    storageKey: 'djopenkb.searchHistory'
                });
            }
        });
    }());

    // add the responsive image class to all images
    $('.body_text img').each(function(){
        $(this).addClass('img-responsive');
    });

    // make all links in articles open in new window/tab
    if(config.links_blank_page === true){
        $('.body_text a').attr('target', '_blank');
    }

    // setup mermaid charting
    if(typeof mermaid !== 'undefined' && config.mermaid){
        //defaults - can be overridden in config.json by specifying mermaid_options
        //TODO: Consider adding mermaid_options to settings page? 
        var mermaid_opts = {
            "theme" : "forest",
            "flowchart": { "curve": "linear" },
            "gantt": { "axisFormat": "%Y/%m/%d" },
            "sequence": { "actorMargin": 20 },
            "securityLevel": "loose" 
        };
        // Merge mermaid_options into mermaid_opts, recursively
        $.extend( true, mermaid_opts, config.mermaid_options || {} );
        mermaid_opts.startOnLoad = true;
        mermaid.initialize(mermaid_opts);
    }

    // add the table class to all tables
    $('table').each(function(){
        $(this).addClass('table table-hover');
    });

    // When the version dropdown changes
    $(document).on('change', '#kb_versions', function(){
        // get the article from the API
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/api/getArticleJson',
            data: {kb_id: $(this).val()}
        })
        .done(function(article){
            $('#frm_kb_title').val(article.kb_title);
            simplemde.value(article.kb_body);
            $('#btnSettingsMenu').trigger('click');
        })
        .fail(function(msg){
            show_notification(msg.responseText, 'danger');
        });
    });

    // Legacy OpenKB typeahead is retained only for old pages that do not use
    // DjOpenKB's newer GET-based title/keyword suggestion endpoint. Running both
    // handlers on the same search bar causes competing dropdown updates.
    var hasModernSearchSuggestions = $('#searchResult[data-suggestions-url]').length > 0;
    if(config.typeahead_search === true && !hasModernSearchSuggestions){
        // on pages which have the legacy search form
        if($('#frm_search').length){
            $('#frm_search').on('keyup', function(){
                if($('#frm_search').val().length > 2){
                    $.ajax({
                        method: 'POST',
                        url: $('#app_context').val() + '/search_api',
                        data: {searchTerm: $('#frm_search').val()}
                    })
                    .done(function(response){
                        if(response.length === 0){
                            $('#searchResult').addClass('hidden');
                        }else{
                            $('.searchResultList').empty();
                            $('.searchResultList').append($('<li class="list-group-item list-group-heading"></li>').text(openKbTranslatedText('data-openkb-search-results', 'Search results')));
                            $.each(response, function(key, value){
                                var faqLink = value.kb_permalink;
                                if(typeof faqLink === 'undefined' || faqLink === ''){
                                    faqLink = value._id;
                                }
                                var searchitem = '<li class="list-group-item"><a href="' + $('#app_context').val() + '/' + config.route_name + '/' + faqLink + '">' + value.kb_title + '</a></li>';
                                $('.searchResultList').append(searchitem);
                            });
                            $('#searchResult').removeClass('hidden');
                        }
                    });
                }else{
                    $('.searchResultList').empty();
                    $('#searchResult').addClass('hidden');
                }
            });
        }
    }

    // setup the push menu
    if($('.toggle-menu').length){
        $('.toggle-menu').jPushMenu({closeOnClickOutside: false});
    }

    // highlight any code blocks
    $('pre code').each(function(i, block){
        hljs.highlightBlock(block);
    });

    // add the table class to all tables
    if(config.add_header_anchors === true){
        $('.body_text > h1, .body_text > h2, .body_text > h3, .body_text > h4, .body_text > h5').each(function(){
            $(this).attr('id', convertToSlug($(this).text()));
            $(this).prepend('<a class="headerAnchor" href="#' + convertToSlug($(this).text()) + '">#</a> ');
        });
    }

    // scroll to hash point
    if(window.location.hash){
        // if element is found, scroll to it
        if($(window.location.hash).length){
            var element = $(window.location.hash);
            $(window).scrollTop(element.offset().top).scrollLeft(element.offset().left);
        }
    }

    // add the token field to the keywords input
    if($('#frm_kb_keywords').length){
        $('#frm_kb_keywords').tokenfield();
    }

    if($('#editor').length){
        // setup editors
        var simplemde = new SimpleMDE({
            element: $('#editor')[0],
            spellChecker: config.enable_spellchecker,
            forceSync: true,
            toolbar: ['bold', 'italic', 'heading', '|', 'quote', 'unordered-list', 'ordered-list', '|', 'link', 'image', '|', 'table', 'horizontal-rule', 'code', 'guide']
        });

        // The Django article add/edit forms provide their own protected image
        // uploader, preview tray, delete control, CSRF handling, and upload limits.
        // Do not attach the legacy OpenKB uploader on those pages or the same
        // paste event will be uploaded twice (including once to the obsolete
        // /file/upload_file endpoint).
        var hasManagedArticleImageUploader = document.getElementById('existingArticleImages') !== null;
        if(
            !hasManagedArticleImageUploader &&
            typeof inlineAttachment !== 'undefined' &&
            inlineAttachment.editors &&
            inlineAttachment.editors.codemirror4
        ){
            inlineAttachment.editors.codemirror4.attach(simplemde.codemirror, {uploadUrl: $('#app_context').val() + '/file/upload_file'});
        }

        // do initial convert on load
        convertTextAreaToMarkdown(true); //true means this is first call - do all rendering    

        // auto scrolls the simpleMDE preview pane
        var preview = document.getElementById('preview');
        if(preview !== null){

            //timed re-render (virtual speedup) - i.e. only call convertTextAreaToMarkdown() after xxxms of inactivity to reduce redraws
            var timer = null;
            //TODO: Consider adding the renderDelayTime to settings
            var renderDelayTime = 500;//only re-render when user stops changing text
            
            // attach to editor changes and update preview
            simplemde.codemirror.on('change', function(){
                if(timer != null)
                    clearTimeout(timer);
                timer = setTimeout(function(){
                    convertTextAreaToMarkdown(false);//pass false to indicate this call is due to a code change
                }, renderDelayTime);
            });

            // One-way block-aware editor -> preview scroll synchronisation.
            //
            // The Markdown editor is the only pane that drives synchronisation.
            // Scrolling the preview never moves the editor, so users can inspect
            // large or image-heavy sections independently. As soon as the editor
            // is scrolled again, the preview re-aligns to the matching source block.
            var suppressPreviewScrollUntil = 0;
            var editorToPreviewFrame = null;
            var lastScrollDriver = 'editor';

            function syncNowMilliseconds(){
                if(window.performance && typeof window.performance.now === 'function'){
                    return window.performance.now();
                }
                return Date.now();
            }

            function clampScrollValue(value, minimum, maximum){
                return Math.min(Math.max(value, minimum), maximum);
            }

            function getEditorGuideOffset(){
                var cmScroller = simplemde.codemirror.getScrollerElement();
                return Math.min(88, Math.max(28, cmScroller.clientHeight * 0.18));
            }

            function getPreviewGuideOffset(){
                return Math.min(88, Math.max(28, preview.clientHeight * 0.18));
            }

            function getPreviewSyncBlocks(){
                var cm = simplemde.codemirror;
                var maxEditorLine = Math.max(cm.lineCount() - 1, 0);
                var previewRect = preview.getBoundingClientRect();
                var blocks = [];

                Array.prototype.slice.call(preview.querySelectorAll('[data-source-line]')).forEach(function(element){
                    // Only use the outermost source marker. This prevents a code
                    // block or other nested rendered element from creating two
                    // competing anchors for the same Markdown block.
                    var markedParent = element.parentElement ? element.parentElement.closest('[data-source-line]') : null;
                    if(markedParent && preview.contains(markedParent)){
                        return;
                    }

                    var startLine = parseInt(element.getAttribute('data-source-line'), 10);
                    var endLine = parseInt(element.getAttribute('data-source-end-line'), 10);
                    if(!Number.isFinite(startLine)){
                        return;
                    }

                    startLine = clampScrollValue(startLine, 0, maxEditorLine);
                    if(!Number.isFinite(endLine) || endLine <= startLine){
                        endLine = startLine + 1;
                    }
                    endLine = clampScrollValue(endLine, startLine + 1, maxEditorLine + 1);

                    var elementRect = element.getBoundingClientRect();
                    var top = elementRect.top - previewRect.top + preview.scrollTop;
                    var bottom = elementRect.bottom - previewRect.top + preview.scrollTop;
                    blocks.push({
                        element: element,
                        startLine: startLine,
                        endLine: endLine,
                        top: Math.max(0, top),
                        bottom: Math.max(top + 1, bottom)
                    });
                });

                blocks.sort(function(left, right){
                    if(left.top === right.top){
                        return left.startLine - right.startLine;
                    }
                    return left.top - right.top;
                });

                // Collapse exact duplicate anchors while keeping the largest
                // visual boundary. This can occur with renderer wrappers.
                var deduplicated = [];
                blocks.forEach(function(block){
                    var previous = deduplicated[deduplicated.length - 1];
                    if(previous && previous.startLine === block.startLine && Math.abs(previous.top - block.top) < 1){
                        previous.endLine = Math.max(previous.endLine, block.endLine);
                        previous.bottom = Math.max(previous.bottom, block.bottom);
                        return;
                    }
                    deduplicated.push(block);
                });

                return deduplicated;
            }

            function findPreviewBlockForEditorLine(line, blocks){
                if(!blocks.length){
                    return null;
                }

                var previous = blocks[0];
                for(var index = 0; index < blocks.length; index++){
                    var block = blocks[index];
                    if(line >= block.startLine && line < block.endLine){
                        return block;
                    }
                    if(block.startLine > line){
                        return previous;
                    }
                    previous = block;
                }
                return blocks[blocks.length - 1];
            }

            function previewPositionForEditorLine(line, block){
                if(!block){
                    return 0;
                }

                var sourceSpan = Math.max(block.endLine - block.startLine, 1);
                if(sourceSpan <= 1){
                    return block.top;
                }

                var ratio = clampScrollValue((line - block.startLine) / sourceSpan, 0, 0.999999);
                return block.top + ((block.bottom - block.top) * ratio);
            }

            function syncPreviewToEditorBlock(){
                var cm = simplemde.codemirror;
                var blocks = getPreviewSyncBlocks();
                if(!blocks.length){
                    return;
                }

                var scrollInfo = cm.getScrollInfo();
                var editorGuide = getEditorGuideOffset();
                var editorLine = cm.lineAtHeight(scrollInfo.top + editorGuide, 'local');
                var block = findPreviewBlockForEditorLine(editorLine, blocks);
                if(!block){
                    return;
                }

                var previewGuide = getPreviewGuideOffset();
                var blockPosition = previewPositionForEditorLine(editorLine, block);
                var maxPreviewScroll = Math.max(preview.scrollHeight - preview.clientHeight, 0);
                var targetTop = clampScrollValue(blockPosition - previewGuide, 0, maxPreviewScroll);

                suppressPreviewScrollUntil = syncNowMilliseconds() + 120;
                preview.scrollTop = targetTop;
            }

            function schedulePreviewFromEditor(){
                if(editorToPreviewFrame !== null){
                    window.cancelAnimationFrame(editorToPreviewFrame);
                }
                editorToPreviewFrame = window.requestAnimationFrame(function(){
                    editorToPreviewFrame = null;
                    syncPreviewToEditorBlock();
                });
            }

            simplemde.codemirror.on('scroll', function(){
                lastScrollDriver = 'editor';
                schedulePreviewFromEditor();
            });

            // Preview scrolling is intentionally independent. Record that the
            // user is inspecting the preview so late-loading images do not pull
            // it back to the editor position. The editor itself is never moved.
            preview.addEventListener('scroll', function(){
                if(syncNowMilliseconds() < suppressPreviewScrollUntil){
                    return;
                }
                lastScrollDriver = 'preview';
            });

            // Images may finish loading after the preview HTML is rendered. Only
            // re-align when the editor was the pane the user last controlled.
            // If the user manually scrolled the preview, leave it exactly there.
            preview.addEventListener('load', function(event){
                if(event.target && event.target.tagName === 'IMG' && lastScrollDriver === 'editor'){
                    schedulePreviewFromEditor();
                }
            }, true);

            // Article image controls can request an immediate preview redraw
            // rather than waiting for the normal typing debounce. Optionally
            // focus the resized image so the new size is visible straight away.
            window.openKbRefreshArticlePreview = function(options){
                options = options || {};
                if(timer !== null){
                    clearTimeout(timer);
                    timer = null;
                }
                convertTextAreaToMarkdown(false);

                window.requestAnimationFrame(function(){
                    var focusSrc = options.focusImageSrc || '';
                    if(focusSrc){
                        var images = Array.prototype.slice.call(preview.querySelectorAll('img'));
                        var targetImage = images.find(function(image){
                            return (image.getAttribute('src') || '') === focusSrc;
                        });

                        if(targetImage){
                            var previewRect = preview.getBoundingClientRect();
                            var imageRect = targetImage.getBoundingClientRect();
                            var imageTop = imageRect.top - previewRect.top + preview.scrollTop;
                            var maxPreviewScroll = Math.max(preview.scrollHeight - preview.clientHeight, 0);
                            var targetTop = clampScrollValue(
                                imageTop - Math.max(24, preview.clientHeight * 0.16),
                                0,
                                maxPreviewScroll
                            );

                            suppressPreviewScrollUntil = syncNowMilliseconds() + 160;
                            preview.scrollTop = targetTop;
                            lastScrollDriver = 'preview';
                            return;
                        }
                    }

                    schedulePreviewFromEditor();
                });
            };
        }
    }

    function saveEditorMarkdown(){
        if(typeof simplemde !== 'undefined' && simplemde && simplemde.codemirror){
            simplemde.codemirror.save();
            $('#editor').val(simplemde.value());
        }
    }

    function submitEditorFormWithButton(submitButton){
        saveEditorMarkdown();

        var form = document.getElementById('edit_form');
        if(!form){
            return;
        }

        // Programmatic form submits do not automatically include the clicked
        // submit button's name/value. Django uses submit_action to distinguish
        // Save draft, Submit for approval, and Submit update for approval, so
        // preserve the actual clicked button before submitting.
        if(form.requestSubmit && submitButton){
            form.requestSubmit(submitButton);
            return;
        }

        if(submitButton && submitButton.name){
            $('#edit_form input.js-clicked-submit-action').remove();
            $('<input>')
                .attr('type', 'hidden')
                .attr('name', submitButton.name)
                .attr('value', submitButton.value)
                .addClass('js-clicked-submit-action')
                .appendTo('#edit_form');
        }

        form.submit();
    }

    // Editor save/submit button clicked
    $(document).on('click', '#frm_edit_kb_save', function(e){
        e.preventDefault();

        if($('#versionSidebar').length && $('#frm_kb_edit_reason').val() === ''){
            // only save if a version is edited
            show_notification(openKbTranslatedText('data-openkb-edit-reason-required', 'Please enter a reason for editing article'), 'danger');
            $('#btnVersionMenu').trigger('click');
            $('#frm_kb_edit_reason').focus();
            return;
        }

        submitEditorFormWithButton(this);
    });

    // Version edit button clicked
    $(document).on('click', '.btnEditVersion', function(e){
        $('#btnVersionMenu').trigger('click');
        $.LoadingOverlay('show', {zIndex: 9999});
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/api/getArticleJson',
            data: {kb_id: $(this).parent().attr('id')}
        })
        .done(function(article){
            $.LoadingOverlay('hide');
            // populate data from fetched article
            $('#frm_kb_title').val(article.kb_title);
            simplemde.value(article.kb_body);
        })
        .fail(function(msg){
            $.LoadingOverlay('hide');
            show_notification(msg, 'danger');
        });
    });

    // Version delete button clicked
    $(document).on('click', '.btnDeleteVersion', function(e){
        var groupElement = $(this).closest('.versionWrapper');
        $('#btnVersionMenu').trigger('click');
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/api/deleteVersion',
            data: {kb_id: $(this).parent().attr('id')}
        })
        .done(function(article){
            // remove the version elements from DOM
            groupElement.remove();
            show_notification(openKbTranslatedText('data-openkb-version-removed-success', 'Version removed successfully'), 'success');
        })
        .fail(function(msg){
            show_notification(JSON.parse(msg.responseText).message, 'danger');
        });
    });


    // if in the editor, trap ctrl+s and cmd+s shortcuts and save the article
    if($('#frm_editor').val() === 'true'){
        $(window).bind('keydown', function(event){
            if(event.ctrlKey || event.metaKey){
                if(String.fromCharCode(event.which).toLowerCase() === 's'){
                    event.preventDefault();
                    $('#frm_edit_kb_save').click();
                }
            }
        });
    }

    // Call to API for a change to the published state of a KB
    $("input[class='published_state']").change(function(){
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/published_state',
            data: {id: this.id, state: this.checked}
        })
        .done(function(msg){
            show_notification(msg, 'success');
        })
        .fail(function(msg){
            show_notification(msg.responseText, 'danger');
        });
    });

    function getPreviewVideoMarkup(value){
        var rawValue = String(value || '').trim();
        if(!rawValue){
            return '';
        }

        var parsed;
        try{
            parsed = new URL(rawValue);
        }catch(error){
            return '';
        }

        if(parsed.protocol !== 'http:' && parsed.protocol !== 'https:'){
            return '';
        }

        var hostname = parsed.hostname.toLowerCase().replace(/\.$/, '');
        var pathParts = parsed.pathname.split('/').filter(function(part){ return part !== ''; });
        var youtubeHosts = [
            'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
            'youtu.be', 'www.youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com'
        ];

        if(youtubeHosts.indexOf(hostname) !== -1){
            var youtubeId = '';
            if(hostname === 'youtu.be' || hostname === 'www.youtu.be'){
                youtubeId = pathParts[0] || '';
            }else if(hostname === 'youtube-nocookie.com' || hostname === 'www.youtube-nocookie.com'){
                if(pathParts.length >= 2 && pathParts[0].toLowerCase() === 'embed'){
                    youtubeId = pathParts[1];
                }
            }else if(parsed.pathname.replace(/\/$/, '').toLowerCase() === '/watch'){
                youtubeId = parsed.searchParams.get('v') || '';
            }else if(pathParts.length >= 2 && ['shorts', 'embed', 'live'].indexOf(pathParts[0].toLowerCase()) !== -1){
                youtubeId = pathParts[1];
            }

            if(/^[A-Za-z0-9_-]{11}$/.test(youtubeId)){
                return '<iframe class="article-video-embed" ' +
                    'src="https://www.youtube-nocookie.com/embed/' + youtubeId + '" ' +
                    'title="YouTube video player" loading="lazy" ' +
                    'referrerpolicy="strict-origin-when-cross-origin" ' +
                    'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" ' +
                    'allowfullscreen></iframe>';
            }
        }

        var vimeoHosts = ['vimeo.com', 'www.vimeo.com', 'player.vimeo.com'];
        if(vimeoHosts.indexOf(hostname) !== -1){
            var vimeoId = '';
            if(hostname === 'player.vimeo.com'){
                if(pathParts.length >= 2 && pathParts[0].toLowerCase() === 'video'){
                    vimeoId = pathParts[1];
                }
            }else{
                for(var index = pathParts.length - 1; index >= 0; index--){
                    if(/^[0-9]{1,20}$/.test(pathParts[index])){
                        vimeoId = pathParts[index];
                        break;
                    }
                }
            }

            if(/^[0-9]{1,20}$/.test(vimeoId)){
                return '<iframe class="article-video-embed" ' +
                    'src="https://player.vimeo.com/video/' + vimeoId + '" ' +
                    'title="Vimeo video player" loading="lazy" ' +
                    'referrerpolicy="strict-origin-when-cross-origin" ' +
                    'allow="autoplay; fullscreen; picture-in-picture; clipboard-write; encrypted-media; web-share" ' +
                    'allowfullscreen></iframe>';
            }
        }

        // Direct media-file URLs are intentionally not rendered in the preview.
        // Loading them could trigger an external HTTP authentication prompt.
        return '';
    }

    function expandPreviewVideoLinks(markdownText){
        var lines = String(markdownText || '').split(/\r?\n/);
        var renderedLines = [];
        var activeFenceChar = '';
        var activeFenceLength = 0;

        lines.forEach(function(line){
            var fenceMatch = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
            if(fenceMatch){
                var fenceToken = fenceMatch[1];
                var fenceChar = fenceToken.charAt(0);
                var fenceLength = fenceToken.length;
                var fenceSuffix = fenceMatch[2].trim();

                if(!activeFenceChar){
                    activeFenceChar = fenceChar;
                    activeFenceLength = fenceLength;
                }else if(fenceChar === activeFenceChar && fenceLength >= activeFenceLength && !fenceSuffix){
                    activeFenceChar = '';
                    activeFenceLength = 0;
                }

                renderedLines.push(line);
                return;
            }

            if(!activeFenceChar){
                var urlMatch = line.match(/^ {0,3}<?(https?:\/\/[^\s<>]+)>? *$/i);
                if(urlMatch){
                    var videoMarkup = getPreviewVideoMarkup(urlMatch[1]);
                    if(videoMarkup){
                        renderedLines.push(videoMarkup);
                        return;
                    }
                }
            }

            renderedLines.push(line);
        });

        return renderedLines.join('\n');
    }

    // convert editor markdown to HTML and display in #preview div
    //firstRender indicates this is a first call (i.e. not a re-render request due to a code editor change) 
    function convertTextAreaToMarkdown(firstRender){
        var classy = window.markdownItClassy;

        var mark_it_down = window.markdownit({html: true, linkify: true, typographer: true, breaks: true});
        mark_it_down.use(classy);

        if(typeof mermaid !== 'undefined' && config.mermaid){
            
            var mermaidChart = function(code) {
                try {
                    mermaid.parse(code)
                    return '<div class="mermaid">'+code+'</div>';
                } catch ({ str, hash }) {
                    return '<pre><code>'+code+'</code></pre>';
                }
            }
            
            var defFenceRules = mark_it_down.renderer.rules.fence.bind(mark_it_down.renderer.rules)
            mark_it_down.renderer.rules.fence = function(tokens, idx, options, env, slf) {
            var token = tokens[idx]
            var code = token.content.trim()
            if (token.info === 'mermaid') {
                return mermaidChart(code)
            }
            var firstLine = code.split(/\n/)[0].trim()
            if (firstLine === 'gantt' || firstLine === 'sequenceDiagram' || firstLine.match(/^graph (?:TB|BT|RL|LR|TD);?$/)) {
                return mermaidChart(code)
            }
            return defFenceRules(tokens, idx, options, env, slf)
            }
        }

        // Add source-line markers to top-level rendered blocks. These markers
        // are preview-only and power block-aware scroll synchronisation.
        var previewMarkdown = expandPreviewVideoLinks(simplemde.value());
        var previewEnvironment = {};
        var previewTokens = mark_it_down.parse(previewMarkdown, previewEnvironment);
        previewTokens.forEach(function(token){
            if(!token.map || token.map.length < 1 || token.level !== 0 || token.type === 'html_block'){
                return;
            }
            if(token.nesting === 1 || token.type === 'fence' || token.type === 'code_block'){
                token.attrSet('data-source-line', String(token.map[0]));
                token.attrSet('data-source-end-line', String(token.map[1] || (token.map[0] + 1)));
            }
        });

        // markdown-it does not apply token attributes to raw html_block tokens,
        // so wrap those blocks in a harmless preview-only source anchor. This is
        // especially important for resized raw <img> tags.
        var defaultHtmlBlockRule = mark_it_down.renderer.rules.html_block;
        mark_it_down.renderer.rules.html_block = function(tokens, idx, options, env, slf){
            var rendered = defaultHtmlBlockRule
                ? defaultHtmlBlockRule(tokens, idx, options, env, slf)
                : tokens[idx].content;
            var sourceLine = tokens[idx].map && tokens[idx].map.length ? tokens[idx].map[0] : 0;
            var sourceEndLine = tokens[idx].map && tokens[idx].map.length > 1 ? tokens[idx].map[1] : (sourceLine + 1);
            return '<div class="preview-source-block" data-source-line="' + sourceLine + '" data-source-end-line="' + sourceEndLine + '">' + rendered + '</div>';
        };

        var html = mark_it_down.renderer.render(previewTokens, mark_it_down.options, previewEnvironment);

        // add responsive images and tables
        var fixed_html = html.replace(/<img/g, "<img class='img-responsive' ");
        fixed_html = fixed_html.replace(/<table/g, "<table class='table table-hover' ");

        var cleanHTML = sanitizeHtml(fixed_html, {
            allowedTags: [ 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'p', 'a', 'ul', 'ol',
                'nl', 'li', 'b', 'i', 'strong', 'em', 'strike', 'code', 'hr', 'br', 'div',
                'table', 'thead', 'caption', 'tbody', 'tr', 'th', 'td', 'pre', 'img', 'iframe'
            ],
            // Keep the preview aligned with the stricter server-side Bleach policy.
            // Never allow arbitrary HTML attributes such as onerror/onload/style.
            allowedAttributes: {
                'a': [ 'href', 'title' ],
                'img': [ 'src', 'alt', 'title', 'class', 'width' ],
                'code': [ 'class', 'data-source-line', 'data-source-end-line' ],
                'pre': [ 'class', 'data-source-line', 'data-source-end-line' ],
                'div': [ 'class', 'data-source-line', 'data-source-end-line' ],
                'p': [ 'data-source-line', 'data-source-end-line' ],
                'blockquote': [ 'data-source-line', 'data-source-end-line' ],
                'ul': [ 'data-source-line', 'data-source-end-line' ],
                'ol': [ 'data-source-line', 'data-source-end-line' ],
                'table': [ 'class', 'data-source-line', 'data-source-end-line' ],
                'th': [ 'align', 'colspan', 'rowspan' ],
                'td': [ 'align', 'colspan', 'rowspan' ],
                'h1': [ 'id', 'data-source-line', 'data-source-end-line' ],
                'h2': [ 'id', 'data-source-line', 'data-source-end-line' ],
                'h3': [ 'id', 'data-source-line', 'data-source-end-line' ],
                'h4': [ 'id', 'data-source-line', 'data-source-end-line' ],
                'h5': [ 'id', 'data-source-line', 'data-source-end-line' ],
                'h6': [ 'id', 'data-source-line', 'data-source-end-line' ],
                'iframe': [ 'src', 'class', 'title', 'loading', 'referrerpolicy', 'allow', 'allowfullscreen' ]
            },
            allowedSchemes: [ 'http', 'https', 'mailto' ]
        });

        // sanitize-html restricts attribute names and URL schemes. Apply the same
        // provider/source checks as the backend before anything is inserted into
        // the live preview DOM, so raw Markdown HTML cannot create arbitrary
        // external frames, media requests, or remote tracking images.
        var previewContainer = document.createElement('div');
        previewContainer.innerHTML = cleanHTML;

        Array.prototype.slice.call(previewContainer.querySelectorAll('iframe')).forEach(function(frame){
            var src = frame.getAttribute('src') || '';
            var parsed;
            try{
                parsed = new URL(src, window.location.origin);
            }catch(error){
                frame.remove();
                return;
            }

            var hostname = parsed.hostname.toLowerCase().replace(/\.$/, '');
            var pathParts = parsed.pathname.split('/').filter(function(part){ return part !== ''; });
            var youtubeId = '';
            var vimeoId = '';

            if(parsed.protocol === 'https:' && hostname === 'www.youtube-nocookie.com' &&
                    !parsed.search && !parsed.hash && pathParts.length === 2 && pathParts[0] === 'embed' &&
                    /^[A-Za-z0-9_-]{11}$/.test(pathParts[1])){
                youtubeId = pathParts[1];
            }else if(parsed.protocol === 'https:' && hostname === 'player.vimeo.com' &&
                    !parsed.search && !parsed.hash && pathParts.length === 2 && pathParts[0] === 'video' &&
                    /^[0-9]{1,20}$/.test(pathParts[1])){
                vimeoId = pathParts[1];
            }

            if(!youtubeId && !vimeoId){
                frame.remove();
                return;
            }

            // Rebuild approved iframe attributes instead of trusting attributes
            // supplied in raw article HTML.
            Array.prototype.slice.call(frame.attributes).forEach(function(attribute){
                frame.removeAttribute(attribute.name);
            });
            frame.setAttribute('class', 'article-video-embed');
            frame.setAttribute('loading', 'lazy');
            frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
            frame.setAttribute('allowfullscreen', '');

            if(youtubeId){
                frame.setAttribute('src', 'https://www.youtube-nocookie.com/embed/' + youtubeId);
                frame.setAttribute('title', 'YouTube video player');
                frame.setAttribute('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share');
            }else{
                frame.setAttribute('src', 'https://player.vimeo.com/video/' + vimeoId);
                frame.setAttribute('title', 'Vimeo video player');
                frame.setAttribute('allow', 'autoplay; fullscreen; picture-in-picture; clipboard-write; encrypted-media; web-share');
            }
        });

        Array.prototype.slice.call(previewContainer.querySelectorAll('img')).forEach(function(image){
            var src = image.getAttribute('src') || '';
            var safeUpload = /^\/wiki\/uploads\/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|gif|webp)$/i.test(src);
            if(!safeUpload){
                image.removeAttribute('src');
            }

            // Match the server-side image width policy. Invalid raw HTML width
            // values are removed instead of being allowed into the live preview.
            var widthValue = image.getAttribute('width');
            if(widthValue !== null){
                var normalizedWidth = String(widthValue).trim();
                var parsedWidth = parseInt(normalizedWidth, 10);
                if(!/^\d+$/.test(normalizedWidth) || parsedWidth < 1 || parsedWidth > 1200){
                    image.removeAttribute('width');
                }else{
                    image.setAttribute('width', String(parsedWidth));
                }
            }
        });

        $('#preview').html(previewContainer.innerHTML);

        // re-hightlight the preview
        $('pre code').each(function(i, block){
            hljs.highlightBlock(block);
        });

        if(!firstRender && typeof mermaid !== 'undefined' && (config.mermaid && config.mermaid_auto_update)) {
            mermaid.init();//when this is not first render AND mermaid_auto_update==true, re-init mermaid charts (render code changes)
        }

    }

    // user up vote clicked
    $(document).on('click', '#btnUpvote', function(){
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/vote',
            data: {'doc_id': $('#doc_id').val(), 'vote_type': 'upvote'}
        })
        .done(function(msg){
            show_notification(msg, 'success', true);
        })
        .fail(function(msg){
            show_notification(msg.responseText, 'danger');
        });
    });

    // user down vote clicked
    $(document).on('click', '#btnDownvote', function(){
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/vote',
            data: {'doc_id': $('#doc_id').val(), 'vote_type': 'downvote'}
        })
        .done(function(msg){
            show_notification(msg, 'success', true);
        })
        .fail(function(msg){
            show_notification(msg.responseText, 'danger');
        });
    });

    // Call to API to check if a permalink is available
    $('#validate_permalink').click(function(){
        if($('#frm_kb_permalink').val() !== ''){
            $.ajax({
                method: 'POST',
                url: $('#app_context').val() + '/api/validate_permalink',
                data: {'permalink': $('#frm_kb_permalink').val(), 'doc_id': $('#frm_kb_id').val()}
            })
            .done(function(msg){
                show_notification(msg, 'success');
            })
            .fail(function(msg){
                show_notification(msg.responseText, 'danger');
            });
        }else{
            show_notification(openKbTranslatedText('data-openkb-permalink-required', 'Please enter a permalink to validate'), 'danger');
        }
    });

    // generates a random permalink
    $('#generate_permalink').click(function(){
        var min = 100000;
        var max = 999999;
        var num = Math.floor(Math.random() * (max - min + 1)) + min;
        $('#frm_kb_permalink').val(num);
    });

    // function to slugify strings
    function slugify(str) {
        var $slug = '';
        var trimmed = $.trim(str);
        $slug = trimmed.replace(/[^a-z0-9-æøå]/gi, '-').
        replace(/-+/g, '-').
        replace(/^-|-$/g, '').
        replace(/æ/gi, 'ae').
        replace(/ø/gi, 'oe').
        replace(/å/gi, 'a');
        return $slug.toLowerCase();
    }

    // generates a permalink from title with form validation
    $('#frm_kb_title').change(function(){
        var title = $(this).val();
        if (title && title.length > 5) {
            $('#generate_permalink_from_title').removeClass('disabled');
            $('#generate_permalink_from_title').click(function(){
                var title = $('#frm_kb_title').val();
                if (title && title.length > 5) {
                    $('#frm_kb_permalink').val(slugify(title));
                }
            });
        } else {
            $('#generate_permalink_from_title').addClass('disabled');
        }
    });

    // applies an article filter
    $('#btn_articles_filter').click(function(){
        window.location.href = $('#app_context').val() + '/articles/' + encodeURIComponent($('#article_filter').val());
    });

    // resets the article filter
    $('#btn_articles_reset').click(function(){
        window.location.href = $('#app_context').val() + '/articles';
    });

    // search button click event
    $('#btn_search').click(function(event){
        if($('#frm_search').val() === ''){
            show_notification(openKbTranslatedText('data-openkb-search-value-required', 'Please enter a search value'), 'danger');
            event.preventDefault();
        }
    });

    if($('#input_notify_message').val() !== ''){
        // save values from inputs
        var message_val = $('#input_notify_message').val();
        var message_type_val = $('#input_notify_message_type').val();

        // clear inputs
        $('#input_notify_message').val('');
        $('#input_notify_message_type').val('');

        // alert
        show_notification(message_val, message_type_val, false);
    }
});

// Calls the API to delete a file
$(document).on('click', '.file_delete_confirm', function(e){
    e.preventDefault();
    var fileId = $(this).attr('data-id');
    var filePath = $(this).attr('data-path');

    if(window.confirm(openKbTranslatedText('data-openkb-delete-file-confirm', 'Are you sure you want to delete the file?'))){
        $.ajax({
            method: 'POST',
            url: $('#app_context').val() + '/file/delete',
            data: {img: filePath}
        })
        .done(function(msg){
            $('#file-' + fileId).remove();
            show_notification(msg, 'success');
        })
        .fail(function(msg){
            show_notification(msg, 'danger');
        });
    }
});

// show notification popup
function show_notification(msg, type, reload_page){
    reload_page = reload_page || false;

    if(!msg){
        return;
    }

    $('#notify_message').stop(true, true);
    $('#notify_message').removeClass();
    $('#notify_message').addClass('notify_message-' + type);
    var closeLabel = $('<div>').text(openKbTranslatedText('data-openkb-close-label', 'Close')).html();
    $('#notify_message').html(
        '<span class="notify-message-text">' + msg + '</span>' +
        '<button type="button" class="notify-message-close" aria-label="' + closeLabel + '">&times;</button>'
    );

    $('#notify_message').css('display', 'none');
    $('#notify_message').slideDown(300).delay(5000).slideUp(300, function(){
        if(reload_page === true){
            location.reload();
        }
    });
}

$(document).on('click', '.notify-message-close', function(e){
    e.preventDefault();
    $('#notify_message').stop(true, true).slideUp(200);
});

function search_form(id){
    $('form#' + id).submit();
}

function convertToSlug(text){
    return text
        .toLowerCase()
        .replace(/ /g, '-')
        .replace(/[^\w-]+/g, '');
}