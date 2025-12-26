/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

if (ato) {
    window.player = videojs('player', {
	'aspectRatio': '16:9',
	'autoplay': 'any',
        'preload': 'metadata'
    })
} else {
    window.player = videojs('player', {
	'aspectRatio': '16:9',
        'preload': 'metadata'
    })
}

// Buffering logic: Aggressive accumulation for slow connections
window.player.ready(function() {
    var player = this;
    
    // Add buffering overlay (text based)
    var overlay = document.createElement('div');
    overlay.className = 'vjs-buffering-overlay';
    player.el().appendChild(overlay);

    var targetBuffer = 2.5;
    var checkInterval = null;
    var userPaused = false;

    function updateProgress() {
        var buffered = player.buffered();
        var currentTime = player.currentTime();
        var duration = player.duration();
        var bufferedEnd = 0;

        if (buffered.length > 0) {
            for (var i = 0; i < buffered.length; i++) {
                if (buffered.start(i) <= currentTime + 1.0) {
                    bufferedEnd = Math.max(bufferedEnd, buffered.end(i));
                }
            }
        }

        var available = Math.max(0, bufferedEnd - currentTime);
        overlay.innerHTML = 'Buffering: ' + available.toFixed(1) + 's / ' + targetBuffer.toFixed(1) + 's';
        
        if ((available >= targetBuffer) || (duration > 0 && bufferedEnd >= duration - 0.5)) {
            stopBuffering();
        }
    }

    function startBuffering() {
        if (checkInterval) return;
        
        player.addClass('vjs-custom-buffering');
        player.loadingSpinner.show();
        
        checkInterval = setInterval(updateProgress, 500);
        updateProgress();
    }

    function stopBuffering() {
        if (!checkInterval) return;
        
        clearInterval(checkInterval);
        checkInterval = null;
        
        player.removeClass('vjs-custom-buffering');
        player.loadingSpinner.hide();
        
        if (!userPaused) {
            player.play().catch(function(error) {
                // If play fails here, it's likely a browser policy issue.
                // The big play button should naturally be handled by video.js.
            });
        }
    }

    player.on('pause', function() {
        if (!checkInterval) userPaused = true;
    });

    player.on('play', function() {
        userPaused = false;
        // Check if we have enough buffer when user clicks play
        var buffered = player.buffered();
        var currentTime = player.currentTime();
        var bufferedEnd = 0;
        if (buffered.length > 0) {
            for (var i = 0; i < buffered.length; i++) {
                if (buffered.start(i) <= currentTime + 1.0) {
                    bufferedEnd = Math.max(bufferedEnd, buffered.end(i));
                }
            }
        }

        if (bufferedEnd - currentTime < targetBuffer && (player.duration() - currentTime > targetBuffer)) {
            player.pause();
            startBuffering();
        }
    });

    // Force initial buffering only if autoplay is active
    player.on('loadstart', function() {
        userPaused = false;
        if (player.autoplay()) {
            startBuffering();
        }
    });

    player.on('waiting', function() {
        // If we are already playing but hit a stall
        if (!player.paused() || checkInterval) {
            startBuffering();
        }
    });

    player.on('playing', function() {
        if (checkInterval) {
             // If we somehow started playing without enough buffer, force wait
             var buffered = player.buffered();
             var currentTime = player.currentTime();
             var bufferedEnd = 0;
             if (buffered.length > 0) {
                 for (var i = 0; i < buffered.length; i++) {
                     if (buffered.start(i) <= currentTime + 0.5) {
                         bufferedEnd = Math.max(bufferedEnd, buffered.end(i));
                     }
                 }
             }
             if (bufferedEnd - currentTime < 1.0 && (player.duration() - currentTime > 2)) {
                 player.pause();
                 startBuffering();
             } else {
                 stopBuffering();
             }
        }
    });

    player.on('dispose', function() {
        if (checkInterval) clearInterval(checkInterval);
    });
});

/* resolution switch */
window.player.videoJsResolutionSwitcher()

fetch("/res/danmaku/" + current_vid + ':' + idx).then(r => r.json().then(ds => {
    /* Initialize danmaku display. */
    window.dm = new Danmaku({
	container: document.getElementById('player'),
	media: document.getElementById('player_html5_api'),
	comments: ds,
    })
    
    window.dm_status = true
    /* Hook the view resize event so danmaku fit in the container. */
    window.addEventListener('resize', mutations => {
	window.dm.resize()
    })
}))

/* custom controls */
var ButtonComp = videojs.getComponent('Button')

var danmakuSwitch = new ButtonComp(player, {
    clickHandler: function(event) {
	if (window.dm_status)
	    window.dm.hide()
	else
	    window.dm.show()
	window.dm_status = !window.dm_status
    }
})

danmakuSwitch.addClass('danmakuSwitchBtn')
window.player.controlBar.addChild(danmakuSwitch, {}, 2)
document.querySelector('.danmakuSwitchBtn .vjs-icon-placeholder').innerHTML =
    '<i class="icon ion-md-easel"></i>'

/* auto next */

if (total_pages > 1) {
    document.getElementById('continue').checked = ato

    document.getElementById('continue').addEventListener('click', function () {
	var url = '/video/' + current_vid + ':' + idx + '?listen=1'
	if (document.getElementById('continue').checked)
	    url += '&ato=1'
	document.getElementById('avswitch').href = url
    })

    window.player.on('ended', function () {
	if (!document.getElementById('continue').checked)
	    return

	if (++idx == total_pages)
	    return
	
	window.location.href = '/video/' + current_vid + ':' + idx + '?ato=1'
    })
}

/* @license-end */
