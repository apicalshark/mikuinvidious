    // 1. Get the real FLV URL (You usually need to fetch this from Bilibili API first)
    // For this example, let's assume you already resolved the final flv url
    const realBilibiliFlvUrl = "https://d1--cn-gotcha01.bilivideo.com/live-bvc/xxxx.flv";

    console.log(`[Proxy] New Client Connected: ${clientReq.socket.remoteAddress}`);

    // 2. Connect to Bilibili
    const upstreamReq = https.request(realBilibiliFlvUrl, {
        headers: BILIBILI_HEADERS,
        method: 'GET'
    }, (upstreamRes) => {
        
        // 3. Pipe Headers
        // Pass important headers like Content-Type (video/x-flv) to the client
        clientRes.writeHead(upstreamRes.statusCode, {
            'Content-Type': 'video/x-flv',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*' // CORS for your mpegts.js
        });

        // 4. Pipe Data Stream (Bilibili -> Proxy -> Client)
        upstreamRes.pipe(clientRes);

        // 5. Handling Upstream Errors (Bilibili disconnects)
        upstreamRes.on('end', () => {
            console.log('[Proxy] Bilibili ended stream.');
            clientRes.end();
        });
    });

    // ============================================================
    // THE MOST IMPORTANT ALGORITHM PART: CLEANUP
    // ============================================================

    // 6. Handle Client Disconnect (User Pauses / Closes Tab)
    clientReq.on('close', () => {
        console.log('[Proxy] Client disconnected. Killing Bilibili connection to save bandwidth.');
        // IMMEDIATE KILL: Destroy the connection to Bilibili.
        // If you don't do this, your server will keep downloading GBs of video for nobody.
        upstreamReq.destroy(); 
    });

    // 7. Handle Errors
    upstreamReq.on('error', (e) => {
        console.error('[Proxy] Upstream Error:', e.message);
        clientRes.end();
    });

    // Initiate the request to Bilibili
    upstreamReq.end();
});