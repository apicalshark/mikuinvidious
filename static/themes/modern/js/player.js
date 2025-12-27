/* @license magnet:?xt=urn:btih:0b31508aeb0634b347b8270c7bee4d411b5d4109&dn=agpl-3.0.txt AGPL-3.0 */

'use strict';

async function initMikuPlayer() {
    // Wait for media-chrome to be defined
    await customElements.whenDefined('media-controller');

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
        setupLivePlayer(video, qualityList, label);
    } else {
        setupVodPlayer(video, qualityList, label);
        setupAutoNext(video);
        
        // Handle FLV VODs with same buffer logic
        const currentSrc = video.src;
        if (currentSrc.includes('.flv') && flvjs.isSupported()) {
            const flvPlayer = flvjs.createPlayer({
                type: 'flv',
                url: currentSrc
            }, {
                enableStashBuffer: true,
                stashInitialSize: 2048 * 1024, // 2MB
                autoCleanupSourceBuffer: true
            });
            flvPlayer.attachMediaElement(video);
            flvPlayer.load();
            window.flvPlayer = flvPlayer;
        }

        // 10s Buffer Limit for regular VODs (Monitoring)
        video.addEventListener('progress', () => {
            if (video.buffered.length > 0) {
                const end = video.buffered.end(video.buffered.length - 1);
                const bufferLen = end - video.currentTime;
                
                // If buffer exceeds 10s and it's a VOD, the browser usually manages it,
                // but we can log or trigger a "low-data" mode if needed.
                if (bufferLen > 10 && !window.is_live) {
                    // console.log("Buffer target reached (10s)");
                }
            }
        });
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

function setupLivePlayer(video, list, label) {
    if (window.isSettingUp) return;
    window.isSettingUp = true;

    // Get the first supported source to check format
    const firstSrc = window.supported_src && window.supported_src[0];
    const liveUrl = firstSrc ? `/proxy/live/${current_vid}_${firstSrc.quality}` : `/proxy/live/${current_vid}`;
    const isHls = firstSrc && firstSrc.url && firstSrc.url.includes('.m3u8');

    // Clean up any existing players
    if (window.hls) {
        window.hls.destroy();
        window.hls = null;
    }
    if (window.flvPlayer) {
        window.flvPlayer.detachMediaElement();
        window.flvPlayer.destroy();
        window.flvPlayer = null;
    }
    if (window.liveInterval) {
        clearInterval(window.liveInterval);
        window.liveInterval = null;
    }

    // Add resume logic listeners once
    if (!video.resumeEventsAdded) {
        video.addEventListener('play', () => {
            // If it's live and we were paused for a long time (>60s) or have an error
            const isStale = video.lastPauseTime && (Date.now() - video.lastPauseTime > 60000);
            const isDead = video.error || (window.flvPlayer && window.flvPlayer.readyState === 0);
            
            if (window.is_live && (isStale || isDead)) {
                video.lastPauseTime = null;
                console.log("[Player] Reconnecting to live stream...");
                setupLivePlayer(video, list, label);
            }
        });
        video.addEventListener('pause', () => {
            video.lastPauseTime = Date.now();
        });
        video.resumeEventsAdded = true;
    }

    if (isHls) {
        if (Hls.isSupported()) {
            const hls = new Hls({
                enableWorker: true,
                lowLatencyMode: true,
                backBufferLength: 60
            });
            hls.loadSource(liveUrl);
            hls.attachMedia(video);
            window.hls = hls;

            hls.on(Hls.Events.MANIFEST_PARSED, () => {
                updateLiveQualityMenu(video, hls, null, list, label, true);
                video.play().catch(() => {});
            });

            hls.on(Hls.Events.LEVEL_SWITCHED, (event, data) => {
                if (hls.autoLevelEnabled) {
                    const level = hls.levels[data.level];
                    label.innerText = `自动 (${level.height}p)`;
                }
            });
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = liveUrl;
            video.addEventListener('loadedmetadata', () => {
                video.play().catch(() => {});
            });
        }
    } else if (flvjs.isSupported()) {
        const flvPlayer = flvjs.createPlayer({
            type: 'flv',
            url: liveUrl,
            isLive: true
        }, {
            enableStashBuffer: true,
            stashInitialSize: 2048 * 1024, // 2MB initial stash for slow connections
            fixAudioTimestampGap: false,
            autoCleanupSourceBuffer: true
        });
        flvPlayer.attachMediaElement(video);
        flvPlayer.load();
        flvPlayer.play().catch(() => {});
        window.flvPlayer = flvPlayer;

        // Latency Manager for FLV
        window.liveInterval = setInterval(() => {
            if (flvPlayer && video.buffered.length > 0) {
                const end = video.buffered.end(video.buffered.length - 1);
                const diff = end - video.currentTime;
                if (diff > 8) {
                    // Too much latency, jump forward but keep 2s buffer
                    video.currentTime = end - 2;
                } else if (diff > 3) {
                    // Start catching up if gap is > 3s
                    video.playbackRate = 1.08;
                } else {
                    video.playbackRate = 1.0;
                }
            }
        }, 3000);
        
        updateLiveQualityMenu(video, null, flvPlayer, list, label, false);
    }
    window.isSettingUp = false;
}

function updateLiveQualityMenu(video, hls, flvPlayer, list, label, isHls) {
    if (!list || !window.supported_src) return;
    list.innerHTML = '';

    if (isHls && hls) {
        // HLS Quality Logic
        const autoBtn = createOption('自动', -1, () => {
            hls.currentLevel = -1;
            label.innerText = '自动';
        }, list);
        if (hls.currentLevel === -1) autoBtn.classList.add('active');
        list.appendChild(autoBtn);

        hls.levels.forEach((level, index) => {
            const name = `${level.height}p`;
            const btn = createOption(name, index, () => {
                hls.currentLevel = index;
                label.innerText = name;
            }, list);
            if (hls.currentLevel === index) btn.classList.add('active');
            list.appendChild(btn);
        });
    } else {
        // FLV / Manual Quality Switch Logic
        const firstSrc = window.supported_src[0];
        window.supported_src.forEach((src) => {
            const btn = createOption(src.new_description, src.quality, () => {
                const newUrl = `/proxy/live/${current_vid}_${src.quality}`;
                if (window.flvPlayer) {
                    window.flvPlayer.detachMediaElement();
                    window.flvPlayer.destroy();
                    if (window.liveInterval) {
                        clearInterval(window.liveInterval);
                        window.liveInterval = null;
                    }
                    const newFlvPlayer = flvjs.createPlayer({
                        type: 'flv',
                        url: newUrl,
                        isLive: true
                    }, {
                        enableStashBuffer: true,
                        stashInitialSize: 2048 * 1024,
                        fixAudioTimestampGap: false,
                        autoCleanupSourceBuffer: true
                    });
                    newFlvPlayer.attachMediaElement(video);
                    newFlvPlayer.load();
                    newFlvPlayer.play().catch(() => {});
                    window.flvPlayer = newFlvPlayer;

                    window.liveInterval = setInterval(() => {
                        if (newFlvPlayer && video.buffered.length > 0) {
                            const end = video.buffered.end(video.buffered.length - 1);
                            const diff = end - video.currentTime;
                            if (diff > 8) {
                                video.currentTime = end - 2;
                            } else if (diff > 3) {
                                video.playbackRate = 1.08;
                            } else {
                                video.playbackRate = 1.0;
                            }
                        }
                    }, 3000);
                } else {
                    video.src = newUrl;
                    video.load();
                    video.play().catch(() => {});
                }
                label.innerText = src.new_description;
            }, list);
            
            // Check if this is the current quality
            if (flvPlayer && flvPlayer._type === 'flv' && flvPlayer._dataSource.url.includes(`_${src.quality}`)) {
                btn.classList.add('active');
                label.innerText = src.new_description;
            } else if (!flvPlayer && (video.src.includes(`_${src.quality}`) || (src.quality === firstSrc.quality && !video.src.includes('_')))) {
                btn.classList.add('active');
                label.innerText = src.new_description;
            }
            list.appendChild(btn);
        });
    }
}

function setupVodQuality(video, list, label) {
    if (!list || !window.supported_src) return;
    list.innerHTML = '';
    const sorted = [...window.supported_src].sort((a, b) => b.quality - a.quality);
    sorted.forEach((src) => {
        const btn = createOption(src.new_description, src.quality, () => {
            const time = video.currentTime, paused = video.paused;
            video.src = `/proxy/video/${current_vid}_${idx}_${src.quality}`;
            const onLoaded = () => { video.currentTime = time; if (!paused) video.play().catch(() => {}); video.removeEventListener('loadedmetadata', onLoaded); };
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
    btn.onclick = (e) => { 
        e.stopPropagation(); 
        onClick(); 
        list.querySelectorAll('button').forEach(b => b.classList.remove('active')); 
        btn.classList.add('active'); 
        toggleQualityMenu(false, null, document.getElementById('quality-menu')); 
    };
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