/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

/* Buffering logic */
(function() {
    var player = document.getElementById('player');
    var indicator = document.getElementById('audio-buffering');
    var progressText = document.getElementById('audio-progress-text');
    var targetBuffer = 5.0;
    var checkInterval = null;
    var isBuffering = false;

    function checkBuffer() {
        var buffered = player.buffered;
        var currentTime = player.currentTime;
        var duration = player.duration;
        
        if (buffered.length > 0) {
            var bufferedEnd = 0;
            for (var i = 0; i < buffered.length; i++) {
                if (buffered.start(i) <= currentTime + 1.0) {
                    bufferedEnd = Math.max(bufferedEnd, buffered.end(i));
                }
            }

            var available = Math.max(0, bufferedEnd - currentTime);
            progressText.innerHTML = 'Buffering: ' + available.toFixed(1) + 's / ' + targetBuffer.toFixed(1) + 's';

            if ((available >= targetBuffer) || (duration > 0 && bufferedEnd >= duration - 0.5)) {
                stopBuffering();
            }
        } else {
            progressText.innerHTML = 'Buffering: 0.0s / ' + targetBuffer.toFixed(1) + 's';
        }
    }

    function startBuffering() {
        if (isBuffering) return;
        isBuffering = true;
        indicator.classList.remove('hidden');
        player.pause();
        if (!checkInterval) {
            checkInterval = setInterval(checkBuffer, 500);
        }
        checkBuffer();
    }

    function stopBuffering() {
        if (!isBuffering) return;
        isBuffering = false;
        indicator.classList.add('hidden');
        if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
        }
        player.play().catch(function() {});
    }

    player.addEventListener('play', function() {
        if (isBuffering) return;
        
        var buffered = player.buffered;
        var currentTime = player.currentTime;
        var bufferedEnd = 0;
        if (buffered.length > 0) {
            for (var i = 0; i < buffered.length; i++) {
                if (buffered.start(i) <= currentTime + 1.0) {
                    bufferedEnd = Math.max(bufferedEnd, buffered.end(i));
                }
            }
        }

        if (bufferedEnd - currentTime < targetBuffer && (player.duration - currentTime > targetBuffer)) {
            startBuffering();
        }
    });

    player.addEventListener('waiting', function() {
        startBuffering();
    });

    // Check if autoplay was allowed by browser
    if (player.autoplay) {
        setTimeout(function() {
            if (player.paused && !isBuffering) {
                startBuffering();
            }
        }, 100);
    }
})();

/* auto next */
if (total_pages > 1) {
    document.getElementById('continue').checked = ato

    document.getElementById('continue').addEventListener('click', function () {
	var prefix = current_vid.startsWith('am') ? '/audio_list/' : '/video/'
	var url = prefix + current_vid + ':' + idx
	if (prefix === '/video/') url += '?listen=0'
	if (document.getElementById('continue').checked)
	    url += (url.includes('?') ? '&' : '?') + 'ato=1'
	document.getElementById('avswitch').href = url
    })

    document.getElementById('player').addEventListener('ended', function () {
	if (!document.getElementById('continue').checked)
	    return

	if (++idx == total_pages)
	    return
	
	var prefix = current_vid.startsWith('am') ? '/audio_list/' : '/video/'
	var url = prefix + current_vid + ':' + idx + '?listen=1&ato=1'
	if (current_vid.startsWith('am')) url = prefix + current_vid + ':' + idx + '?ato=1'
	window.location.href = url
    })
}

if (current_vid.startsWith('au') || current_vid.startsWith('am')) {
    var avswitch = document.getElementById('avswitch');
    if (avswitch) avswitch.classList.add('hidden');
}


/* @license-end */