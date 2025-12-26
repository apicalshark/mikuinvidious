/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

if (ato) {
    window.player = videojs('player', {
	'aspectRatio': '16:9',
	'autoplay': 'any',
        'preload': 'metadata',
        'controlBar': {
            'currentTimeDisplay': true,
            'timeDivider': true,
            'durationDisplay': true,
            'remainingTimeDisplay': false
        }
    })
} else {
    window.player = videojs('player', {
	'aspectRatio': '16:9',
        'preload': 'metadata',
        'controlBar': {
            'currentTimeDisplay': true,
            'timeDivider': true,
            'durationDisplay': true,
            'remainingTimeDisplay': false
        }
    })
}

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
