bodyElem = document.getElementsByTagName('body')[0]

if (!localStorage.getItem('theme')) {
	bodyElem.classList.forEach(function (klass) {
		if (klass.startsWith('mdui-theme-layout-'))
			localStorage.setItem('theme', klass.slice(18))
	})
}

if (!localStorage.getItem('color')) {
	bodyElem.classList.forEach(function (klass) {
		if (klass.startsWith('mdui-theme-primary-'))
			localStorage.setItem('color', klass.slice(19))
	})
}

if (!localStorage.getItem('quality'))
	localStorage.setItem('quality', '32')

document.querySelectorAll('.js-only').forEach(x => x.classList.toggle('mdui-hidden'))
document.querySelectorAll('.nojs-only').forEach(x => x.classList.add('mdui-hidden'))

window.qnc = function (quality) {
	document.querySelector(`[href="javascript:qnc(${localStorage.getItem('quality')});"]`)
			.classList.remove('mdui-color-theme')

	localStorage.setItem('quality', quality)

	document.querySelector(`[href="javascript:qnc(${quality});"]`)
			.classList.add('mdui-color-theme')
}

bodyElem.className = `mdui-theme-layout-${localStorage.getItem('theme')}`
	+ ` mdui-theme-primary-${localStorage.getItem('color')}`
	+ ` mdui-theme-accent-${localStorage.getItem('color')}`

window.addEventListener('load', function() {
	document.querySelector(`[value="${localStorage.getItem('color')}"]`)
			.setAttribute('checked', '')
	document.querySelector(`[value="${localStorage.getItem('theme')}"]`)
			.setAttribute('checked', '')

	tf = document.getElementById('theme-sel')
	for (let inpa of tf['elements']['theme-layout']) {
		inpa.onclick = function () {
			newTheme = tf['elements']['theme-layout'].value

			bodyElem.classList.remove(`mdui-theme-layout-${localStorage.getItem('theme')}`)
			bodyElem.classList.add(`mdui-theme-layout-${newTheme}`)

			localStorage.setItem('theme', newTheme)
		}
	}

	cf = document.getElementById('color-sel')
	for (let inpa of cf['elements']['theme-primary']) {
		inpa.onclick = function () {
			newColor = cf['elements']['theme-primary'].value

			bodyElem.classList.remove(`mdui-theme-accent-${localStorage.getItem('color')}`)
			bodyElem.classList.remove(`mdui-theme-primary-${localStorage.getItem('color')}`)
			bodyElem.classList.add(`mdui-theme-accent-${newColor}`)
			bodyElem.classList.add(`mdui-theme-primary-${newColor}`)

			localStorage.setItem('color', newColor)
		}
	}

	document.querySelector(`[href="javascript:qnc(${localStorage.getItem('quality')});"]`)
			.classList.add('mdui-color-theme')
}, false)