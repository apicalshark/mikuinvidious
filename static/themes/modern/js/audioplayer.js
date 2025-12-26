/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

/* Buffering logic */
(function() {
    var player = document.getElementById('player');
    var indicator = document.getElementById('audio-buffering');
    var progressText = document.getElementById('audio-progress-text');
    var targetBuffer = 2.0;
    var checkInterval = null;

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

            var available = bufferedEnd - currentTime;
            progressText.innerHTML = 'Buffering: ' + available.toFixed(1) + 's / ' + targetBuffer.toFixed(1) + 's';

            if ((available >= targetBuffer) || (duration > 0 && bufferedEnd >= duration - 0.5)) {
                indicator.classList.add('hidden');
                player.play().catch(function() {});
                if (checkInterval) {
                    clearInterval(checkInterval);
                    checkInterval = null;
                }
            }
        } else {
            progressText.innerHTML = 'Buffering: 0.0s / ' + targetBuffer.toFixed(1) + 's';
        }
    }

    player.addEventListener('loadstart', function() {
        indicator.classList.remove('hidden');
        player.pause();
        if (!checkInterval) {
            checkInterval = setInterval(checkBuffer, 500);
        }
    });

    player.addEventListener('waiting', function() {
        indicator.classList.remove('hidden');
        player.pause();
        if (!checkInterval) {
            checkInterval = setInterval(checkBuffer, 500);
        }
    });

    player.addEventListener('playing', function() {
        indicator.classList.add('hidden');
        if (checkInterval) {
            clearInterval(checkInterval);
            checkInterval = null;
        }
    });
})();

/* auto next */
if (total_pages > 1) {
    document.getElementById('continue').checked = ato

    document.getElementById('continue').addEventListener('click', function () {
	var url = '/video/' + current_vid + ':' + idx + '?listen=0'
	if (document.getElementById('continue').checked)
	    url += '&ato=1'
	document.getElementById('avswitch').href = url
    })

    document.getElementById('player').addEventListener('ended', function () {
	if (!document.getElementById('continue').checked)
	    return

	if (++idx == total_pages)
	    return
	
	window.location.href = '/video/' + current_vid + ':' + idx + '?listen=1&ato=1'
    })
}

/* @license-end */
