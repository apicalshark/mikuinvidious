/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

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
    if (avswitch) avswitch.style.display = 'none';
}


/* @license-end */
