package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/armon/go-socks5"
)

type Rotator struct {
	ipv4s    []net.IP
	ipv6s    []net.IP
	strategy string
	mu       sync.Mutex

	// State for Round-Robin
	idxV4 int
	idxV6 int

	// State for Periodic
	lastRotate time.Time
	interval   time.Duration
	currentV4  net.IP
	currentV6  net.IP
}

func (r *Rotator) getIP(pool []net.IP, isIPv6 bool) net.IP {
	if len(pool) == 0 {
		return nil
	}

	r.mu.Lock()
	defer r.mu.Unlock()

	switch r.strategy {
	case "round-robin":
		if isIPv6 {
			ip := pool[r.idxV6%len(pool)]
			r.idxV6++
			return ip
		} else {
			ip := pool[r.idxV4%len(pool)]
			r.idxV4++
			return ip
		}

	case "periodic":
		if time.Since(r.lastRotate) > r.interval || (isIPv6 && r.currentV6 == nil) || (!isIPv6 && r.currentV4 == nil) {
			r.lastRotate = time.Now()
			if len(r.ipv4s) > 0 {
				r.currentV4 = r.ipv4s[rand.Intn(len(r.ipv4s))]
			}
			if len(r.ipv6s) > 0 {
				r.currentV6 = r.ipv6s[rand.Intn(len(r.ipv6s))]
			}
			log.Printf("[Rotator] Periodic rotation triggered. New V4: %v, New V6: %v", r.currentV4, r.currentV6)
		}
		if isIPv6 {
			return r.currentV6
		}
		return r.currentV4

	case "random":
		fallthrough
	default:
		return pool[rand.Intn(len(pool))]
	}
}

func (r *Rotator) handleHTTP(c net.Conn, firstByte byte) {
	defer c.Close()

	reader := bufio.NewReader(&bufferedConn{Conn: c, firstByte: firstByte, hasFirst: true})
	req, err := http.ReadRequest(reader)
	if err != nil {
		log.Printf("[HTTP] Error reading request: %v", err)
		return
	}

	if req.Method != http.MethodConnect {
		log.Printf("[HTTP] Unsupported method: %s", req.Method)
		resp := &http.Response{
			StatusCode: http.StatusMethodNotAllowed,
			ProtoMajor: 1,
			ProtoMinor: 1,
		}
		resp.Write(c)
		return
	}

	targetConn, err := r.Dial(context.Background(), "tcp", req.Host)
	if err != nil {
		log.Printf("[HTTP] Failed to dial %s: %v", req.Host, err)
		return
	}
	defer targetConn.Close()

	fmt.Fprintf(c, "HTTP/1.1 200 Connection Established\r\n\r\n")

	errChan := make(chan error, 2)
	cp := func(dst, src net.Conn) {
		_, err := io.Copy(dst, src)
		errChan <- err
	}

	go cp(c, targetConn)
	go cp(targetConn, c)

	<-errChan
}

func (r *Rotator) Dial(ctx context.Context, network, addr string) (net.Conn, error) {
	dialer := &net.Dialer{
		Timeout:   30 * time.Second,
		KeepAlive: 30 * time.Second,
	}

	host, port, _ := net.SplitHostPort(addr)
	isIPv6 := false
	targetAddr := addr

	if ip := net.ParseIP(host); ip != nil {
		if ip.To4() == nil {
			isIPv6 = true
		}
	} else {
		// Preference Logic: If we have IPv6 pool, try to find an AAAA record for the host
		ips, _ := net.LookupIP(host)
		var v4, v6 net.IP
		for _, ip := range ips {
			if ip.To4() == nil {
				v6 = ip
			} else {
				v4 = ip
			}
		}

		// If we have an IPv6 pool AND the target supports IPv6, FORCE use IPv6
		if len(r.ipv6s) > 0 && v6 != nil {
			isIPv6 = true
			targetAddr = net.JoinHostPort(v6.String(), port)
		} else if v4 != nil {
			isIPv6 = false
			targetAddr = net.JoinHostPort(v4.String(), port)
		}
	}

	localIP := r.getIP(func() []net.IP {
		if isIPv6 {
			return r.ipv6s
		}
		return r.ipv4s
	}(), isIPv6)

	if localIP != nil {
		if strings.HasPrefix(network, "tcp") {
			dialer.LocalAddr = &net.TCPAddr{IP: localIP}
		} else if strings.HasPrefix(network, "udp") {
			dialer.LocalAddr = &net.UDPAddr{IP: localIP}
		}
		log.Printf("[Rotator] Outgoing (%s) via %s -> %s (Target: %s)", network, localIP, addr, targetAddr)
	} else {
		log.Printf("[Rotator] Outgoing (%s) via default -> %s", network, addr)
	}

	return dialer.DialContext(ctx, network, targetAddr)
}

func discoverIPs(ifaceName string) ([]net.IP, []net.IP, error) {
	iface, err := net.InterfaceByName(ifaceName)
	if err != nil {
		return nil, nil, err
	}

	addrs, err := iface.Addrs()
	if err != nil {
		return nil, nil, err
	}

	var v4s, v6s []net.IP
	for _, addr := range addrs {
		ipNet, ok := addr.(*net.IPNet)
		if !ok {
			continue
		}
		ip := ipNet.IP
		if ip.IsLoopback() || ip.IsLinkLocalUnicast() {
			continue
		}

		if ip.To4() != nil {
			v4s = append(v4s, ip)
		} else if ip.IsGlobalUnicast() {
			v6s = append(v6s, ip)
		}
	}
	return v4s, v6s, nil
}

func main() {
	rand.Seed(time.Now().UnixNano())

	port := os.Getenv("PORT")
	if port == "" {
		port = "1081"
	}

	var v4s, v6s []net.IP
	ifaceName := os.Getenv("BIND_INTERFACE")
	staticIPs := os.Getenv("BIND_IPS")
	skipIPs := make(map[string]bool)
	if s := os.Getenv("SKIP_IPS"); s != "" {
		for _, ip := range strings.Split(s, ",") {
			skipIPs[strings.TrimSpace(ip)] = true
		}
		log.Printf("[Init] Skipping IPs: %v", os.Getenv("SKIP_IPS"))
	}

	if ifaceName != "" {
		for _, name := range strings.Split(ifaceName, ",") {
			name = strings.TrimSpace(name)
			log.Printf("[Init] Discovering IPs on interface: %s", name)
			v4, v6, err := discoverIPs(name)
			if err != nil {
				log.Printf("[Warning] IP discovery failed for %s: %v", name, err)
				continue
			}
			for _, ip := range v4 {
				if !skipIPs[ip.String()] {
					v4s = append(v4s, ip)
				}
			}
			for _, ip := range v6 {
				if !skipIPs[ip.String()] {
					v6s = append(v6s, ip)
				}
			}
		}
	}

	if staticIPs != "" {
		for _, s := range strings.Split(staticIPs, ",") {
			s = strings.TrimSpace(s)
			if skipIPs[s] {
				continue
			}
			ip := net.ParseIP(s)
			if ip != nil {
				if ip.To4() != nil {
					v4s = append(v4s, ip)
				} else {
					v6s = append(v6s, ip)
				}
			}
		}
	}

	log.Printf("[Init] Rotator initialized with %d IPv4s and %d IPv6s", len(v4s), len(v6s))

	strategy := strings.ToLower(os.Getenv("ROTATION_STRATEGY"))
	if strategy == "" {
		strategy = "random"
	}

	intervalStr := os.Getenv("ROTATION_INTERVAL")
	interval := 5 * time.Minute
	if d, err := time.ParseDuration(intervalStr); err == nil {
		interval = d
	}

	rotator := &Rotator{
		ipv4s:    v4s,
		ipv6s:    v6s,
		strategy: strategy,
		interval: interval,
	}
	log.Printf("[Init] Strategy: %s, Periodic Interval: %v", strategy, interval)

	socksServer, err := socks5.New(&socks5.Config{Dial: rotator.Dial})
	if err != nil {
		log.Fatalf("[Fatal] Failed to create SOCKS5 server: %v", err)
	}

	l, err := net.Listen("tcp", ":"+port)
	if err != nil {
		log.Fatalf("[Fatal] Failed to listen on :%s: %v", port, err)
	}

	log.Printf("[Server] Miku-Proxy-Rotator (Dual-Mode) listening on :%s", port)
	
	for {
		conn, err := l.Accept()
		if err != nil {
			log.Printf("[Error] Accept error: %v", err)
			continue
		}
		
		go func(c net.Conn) {
			buf := make([]byte, 1)
			c.SetReadDeadline(time.Now().Add(2 * time.Second))
			_, err := io.ReadFull(c, buf)
			c.SetReadDeadline(time.Time{})

			if err != nil {
				c.Close()
				return
			}

			if buf[0] == 5 {
				wrapped := &bufferedConn{Conn: c, firstByte: buf[0], hasFirst: true}
				socksServer.ServeConn(wrapped)
			} else if buf[0] == 'C' || buf[0] == 'G' || buf[0] == 'P' {
				log.Printf("[HTTP] Handling HTTP proxy request from %s", c.RemoteAddr())
				rotator.handleHTTP(c, buf[0])
			} else {
				log.Printf("[Warning] Unknown protocol byte: %d from %s", buf[0], c.RemoteAddr())
				c.Close()
			}
		}(conn)
	}
}

type bufferedConn struct {
	net.Conn
	firstByte byte
	hasFirst  bool
}

func (b *bufferedConn) Read(p []byte) (int, error) {
	if b.hasFirst {
		p[0] = b.firstByte
		b.hasFirst = false
		if len(p) == 1 {
			return 1, nil
		}
		n, err := b.Conn.Read(p[1:])
		return n + 1, err
	}
	return b.Conn.Read(p)
}
