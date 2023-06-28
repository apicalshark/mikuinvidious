/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

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
