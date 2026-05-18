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
                '.search-history-dropdown{position:absolute;z-index:10050;background:#fff;border:1px solid #dce4ec;border-radius:4px;box-shadow:0 6px 18px rgba(0,0,0,.16);max-height:260px;overflow:auto;text-align:left;}' +
                '.search-history-dropdown.hidden{display:none;}' +
                '.search-history-title{padding:7px 11px;color:#7b8a8b;font-size:12px;font-weight:700;text-transform:uppercase;border-bottom:1px solid #eef2f4;background:#f8fafb;}' +
                '.search-history-item{display:flex;align-items:center;gap:8px;width:100%;border:0;background:#fff;padding:9px 10px;color:#2c3e50;text-align:left;}' +
                '.search-history-item:hover,.search-history-item:focus{background:#f4f8fb;outline:none;}' +
                '.search-history-term{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
                '.search-history-icon{color:#95a5a6;}' +
                '.search-history-remove{border:0;background:transparent;color:#95a5a6;padding:0 2px;line-height:1;}' +
                '.search-history-remove:hover{color:#c0392b;}' +
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
            var title = options.title || 'Search history';

            var $container = $input.closest('.input-group');
            if(!$container.length){
                $container = $input.parent();
            }
            $container.addClass('search-history-container');

            var $dropdown = $('<div class="search-history-dropdown hidden" role="listbox"></div>');
            $container.append($dropdown);

            function positionDropdown(){
                var inputPosition = $input.position();
                $dropdown.css({
                    left: inputPosition.left,
                    top: inputPosition.top + $input.outerHeight(),
                    width: $input.outerWidth()
                });
            }

            function hideDropdown(){
                $dropdown.addClass('hidden');
            }

            function renderDropdown(){
                var history = getHistory(storageKey);
                var currentValue = $.trim($input.val() || '').toLowerCase();

                if(currentValue){
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

                $dropdown.append('<div class="search-history-title">' + title + '</div>');

                history.forEach(function(item){
                    var $row = $('<button type="button" class="search-history-item" role="option"></button>');
                    var $icon = $('<i class="fa fa-history search-history-icon" aria-hidden="true"></i>');
                    var $term = $('<span class="search-history-term"></span>').text(item);
                    var $remove = $('<button type="button" class="search-history-remove" aria-label="Remove search history item"><i class="fa fa-times"></i></button>');

                    $row.append($icon).append($term).append($remove);

                    $row.on('mousedown', function(event){
                        event.preventDefault();
                    });

                    $row.on('click', function(){
                        $input.val(item).focus();
                        hideDropdown();
                    });

                    $remove.on('click', function(event){
                        event.preventDefault();
                        event.stopPropagation();
                        removeHistoryItem(storageKey, item);
                        renderDropdown();
                    });

                    $dropdown.append($row);
                });

                positionDropdown();
                $dropdown.removeClass('hidden');
            }

            $input.on('focus click keyup', function(event){
                if(event.type === 'keyup' && event.key === 'Escape'){
                    hideDropdown();
                    return;
                }
                renderDropdown();
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

            // Do not enable history for the OpenKB AI question/chatbox input.
            if(inputId === 'openkbAiQuestion' || $(this).closest('#openkbAiBox, .openkb-ai-box, .ai-chatbox, .chatbox').length){
                return;
            }

            if(seenInputs.indexOf(this) === -1){
                seenInputs.push(this);
                setupHistoryDropdown($(this), {
                    storageKey: 'djopenkb.searchHistory',
                    title: 'Search history'
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

    // hookup the typeahead search
    if(config.typeahead_search === true){
        // on pages which have the search form
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
                            $('.searchResultList').append('<li class="list-group-item list-group-heading">Search results</li>');
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

        // setup inline attachments
        inlineAttachment.editors.codemirror4.attach(simplemde.codemirror, {uploadUrl: $('#app_context').val() + '/file/upload_file'});

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

            // Syncs scroll  editor -> preview
            var cScroll = false;
            var pScroll = false;
            simplemde.codemirror.on('scroll', function(v){
                if(cScroll){
                    cScroll = false;
                    return;
                }
                pScroll = true;
                var height = v.getScrollInfo().height - v.getScrollInfo().clientHeight;
                var ratio = parseFloat(v.getScrollInfo().top) / height;
                var move = (preview.scrollHeight - preview.clientHeight) * ratio;
                preview.scrollTop = move;
            });

            // Syncs scroll  preview -> editor
            preview.onscroll = function(){
                if(pScroll){
                    pScroll = false;
                    return;
                }
                cScroll = true;
                var height = preview.scrollHeight - preview.clientHeight;
                var ratio = parseFloat(preview.scrollTop) / height;
                var move = (simplemde.codemirror.getScrollInfo().height - simplemde.codemirror.getScrollInfo().clientHeight) * ratio;
                simplemde.codemirror.scrollTo(0, move);
            };
        }
    }

    // Editor save button clicked
    $(document).on('click', '#frm_edit_kb_save', function(e){
        e.preventDefault();

        if($('#versionSidebar').length){
            // only save if a version is edited
            if($('#frm_kb_edit_reason').val() === ''){
                show_notification('Please enter a reason for editing article', 'danger');
                $('#btnVersionMenu').trigger('click');
                $('#frm_kb_edit_reason').focus();
            }else{
                if(typeof simplemde !== 'undefined' && simplemde && simplemde.codemirror){
                    simplemde.codemirror.save();
                    $('#editor').val(simplemde.value());
                }
                $('#edit_form').submit();
            }
        }else{
            if(typeof simplemde !== 'undefined' && simplemde && simplemde.codemirror){
                simplemde.codemirror.save();
                $('#editor').val(simplemde.value());
            }
            $('#edit_form').submit();
        }
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
            show_notification('Version removed successfully', 'success');
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

        var html = mark_it_down.render(simplemde.value());

        // add responsive images and tables
        var fixed_html = html.replace(/<img/g, "<img class='img-responsive' ");
        fixed_html = fixed_html.replace(/<table/g, "<table class='table table-hover' ");

        var cleanHTML = sanitizeHtml(fixed_html, {
            allowedTags: [ 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'p', 'a', 'ul', 'ol',
                'nl', 'li', 'b', 'i', 'strong', 'em', 'strike', 'code', 'hr', 'br', 'div',
                'table', 'thead', 'caption', 'tbody', 'tr', 'th', 'td', 'pre', 'img', 'iframe'
            ],
            allowedAttributes: false
        });

        $('#preview').html(cleanHTML);

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
            show_notification('Please enter a permalink to validate', 'danger');
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
            show_notification('Please enter a search value', 'danger');
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

    if(window.confirm('Are you sure you want to delete the file?')){
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
    $('#notify_message').html(
        '<span class="notify-message-text">' + msg + '</span>' +
        '<button type="button" class="notify-message-close" aria-label="Close">&times;</button>'
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