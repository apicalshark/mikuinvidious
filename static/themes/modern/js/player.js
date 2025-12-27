/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

'use strict';

async function initMikuPlayer() {
    const promises = [customElements.whenDefined('media-controller')];
    if (document.querySelector('hls-video')) promises.push(customElements.whenDefined('hls-video'));
    if (document.querySelector('flv-video')) promises.push(customElements.whenDefined('flv-video'));
    
    await Promise.all(promises);

    const video = document.getElementById('player');
    const danmakuContainer = document.getElementById('danmaku-container');
    const qualityList = document.getElementById('quality-list');
    const qualityMenu = document.getElementById('quality-menu');
    const qualityBtn = document.getElementById('quality-btn');
    const label = document.getElementById('current-quality-label');
    const controller = document.getElementById('miku-player');

    if (!video) return;

    // 1. Danmaku Setup (Using DOM engine for pixel perfection)
    initDanmaku(video, danmakuContainer, controller);

    // 2. Quality Selection Logic
    if (window.is_live) {
        setupLiveQuality(video, qualityList, label);
    } else {
        setupVodQuality(video, qualityList, label);
        setupAutoNext(video);
    }

    // 3. UI Events
    const dmBtn = document.getElementById('danmaku-toggle');
    if (dmBtn) {
        dmBtn.onclick = (e) => {
            e.stopPropagation();
            toggleDanmaku();
        };
    }

    if (qualityBtn && qualityMenu && controller) {
        qualityBtn.onclick = (e) => {
            e.stopPropagation();
            const isVisible = qualityMenu.classList.contains('opacity-100');
            toggleQualityMenu(!isVisible, qualityBtn, qualityMenu, controller);
        };
        document.addEventListener('click', () => toggleQualityMenu(false, null, qualityMenu, controller));
    }
}

function toggleQualityMenu(show, btn, menu, controller) {
    if (!menu) return;
    if (show && btn && controller) {
        const brect = btn.getBoundingClientRect();
        const crect = controller.getBoundingClientRect();
        
        menu.style.right = (crect.right - brect.right) + 'px';
        menu.style.bottom = (crect.bottom - brect.top + 10) + 'px';

        menu.classList.remove('opacity-0', 'pointer-events-none', 'scale-95');
        menu.classList.add('opacity-100', 'scale-100', 'pointer-events-auto');
    } else {
        menu.classList.add('opacity-0', 'pointer-events-none', 'scale-95');
        menu.classList.remove('opacity-100', 'scale-100', 'pointer-events-auto');
    }
}

function initDanmaku(video, container, controller) {
    if (!container || !video || !controller) return;

    fetch("/res/danmaku/" + current_vid + ':' + idx).then(r => r.json().then(ds => {
        window.dm = new Danmaku({
            container: container,
            media: video,
            comments: ds,
            engine: 'dom'
        });
        
        window.dm_status = true;
        
        const updateSize = () => {
            if (!window.dm || !video || !container) return;
            
            // Get dimensions of the player controller
            const rect = controller.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return;

            const vw = video.videoWidth;
            const vh = video.videoHeight;

            if (!vw || !vh) {
                Object.assign(container.style, { width: '100%', height: '100%', left: '0', top: '0' });
            } else {
                const videoRatio = vw / vh;
                const playerRatio = rect.width / rect.height;

                let dw, dh, left, top;
                if (playerRatio > videoRatio) {
                    dh = rect.height;
                    dw = dh * videoRatio;
                    top = 0;
                    left = (rect.width - dw) / 2;
                } else {
                    dw = rect.width;
                    dh = dw / videoRatio;
                    left = 0;
                    top = (rect.height - dh) / 2;
                }

                Object.assign(container.style, {
                    width: dw + 'px',
                    height: dh + 'px',
                    left: left + 'px',
                    top: top + 'px'
                });
            }
            
            window.dm.resize();
        };

        const ro = new ResizeObserver(() => requestAnimationFrame(updateSize));
        ro.observe(controller);
        
        video.addEventListener('loadedmetadata', updateSize);
        video.addEventListener('resize', updateSize);
        
        // Fullscreen and window resize triggers
        ['fullscreenchange', 'webkitfullscreenchange'].forEach(evt => {
            document.addEventListener(evt, () => {
                updateSize();
                setTimeout(updateSize, 100);
            });
        });

        window.addEventListener('resize', updateSize);
        updateSize();
    }));
}

function setupLiveQuality(video, list, label) {
    if (video.tagName.toLowerCase() === 'hls-video') {
        setupHlsQuality(video, list, label);
    } else {
        // For non-HLS live (e.g. FLV), we use similar logic to VOD but with live proxy URLs
        if (!list || !window.supported_src) return;
        list.innerHTML = '';
        window.supported_src.forEach((src) => {
            const btn = createOption(src.new_description, src.quality, () => {
                video.src = `/proxy/live/${current_vid}_${src.quality}`;
                video.load();
                video.play();
                label.innerText = src.new_description;
            }, list);
            if (video.src.includes(`_${src.quality}`) || (src.quality === 'default' && !video.src.includes('_'))) {
                btn.classList.add('active');
                label.innerText = src.new_description;
            }
            list.appendChild(btn);
        });
    }
}

function setupHlsQuality(video, list, label) {
    const updateMenu = () => {
        const renditions = video.videoRenditions;
        if (!renditions || !list) return;
        list.innerHTML = '';
        const autoBtn = createOption('自动', -1, () => { renditions.selectedIndex = -1; label.innerText = '自动'; }, list);
        if (renditions.selectedIndex === -1) autoBtn.classList.add('active');
        list.appendChild(autoBtn);
        Array.from(renditions).forEach((r, i) => {
            const name = `${r.height}p`;
            const btn = createOption(name, i, () => { renditions.selectedIndex = i; label.innerText = name; }, list);
            if (renditions.selectedIndex === i) btn.classList.add('active');
            list.appendChild(btn);
        });
    };
    video.videoRenditions.onaddrendition = video.videoRenditions.onremoverendition = updateMenu;
    video.addEventListener('resize', () => { if (video.videoRenditions.selectedIndex === -1 && video.videoHeight > 0) label.innerText = `自动 (${video.videoHeight}p)`; });
    setTimeout(updateMenu, 1500); 
}

function setupVodQuality(video, list, label) {
    if (!list || !window.supported_src) return;
    list.innerHTML = '';
    const sorted = [...window.supported_src].sort((a, b) => b.quality - a.quality);
    sorted.forEach((src) => {
        const btn = createOption(src.new_description, src.quality, () => {
            const time = video.currentTime, paused = video.paused;
            video.src = `/proxy/video/${current_vid}_${idx}_${src.quality}`;
            const onLoaded = () => { video.currentTime = time; if (!paused) video.play(); video.removeEventListener('loadedmetadata', onLoaded); };
            video.addEventListener('loadedmetadata', onLoaded);
            label.innerText = src.new_description;
        }, list);
        if (video.src.includes(`_${src.quality}`)) { btn.classList.add('active'); label.innerText = src.new_description; }
        list.appendChild(btn);
    });
}

function createOption(text, val, onClick, list) {
    const btn = document.createElement('button');
    btn.className = 'w-full text-left px-4 py-2.5 text-xs text-white/70 hover:bg-white/10 hover:text-white transition-all rounded-xl flex items-center justify-between group';
    btn.innerHTML = `<span>${text}</span><i class="icon ion-md-checkmark text-primary opacity-0 group-[.active]:opacity-100"></i>`;
    btn.onclick = (e) => { e.stopPropagation(); onClick(); list.querySelectorAll('button').forEach(b => b.classList.remove('active')); btn.classList.add('active'); toggleQualityMenu(false, null, document.getElementById('quality-menu')); };
    return btn;
}

function toggleDanmaku() {
    if (!window.dm) return;
    const btn = document.getElementById('danmaku-toggle');
    if (window.dm_status) { window.dm.hide(); if (btn) btn.style.opacity = '0.4'; }
    else { window.dm.show(); if (btn) btn.style.opacity = '1.0'; }
    window.dm_status = !window.dm_status;
}

function setupAutoNext(video) {
    video.addEventListener('ended', function () {
        if (document.getElementById('continue')?.checked && ++idx < total_pages) {
            window.location.href = `/video/${current_vid}:${idx}?ato=1`;
        }
    });
}

document.addEventListener('DOMContentLoaded', initMikuPlayer);

/* @license-end */
