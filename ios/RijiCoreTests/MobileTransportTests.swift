import XCTest
import Foundation
import Network
@testable import RijiCore

private actor MobileWire: MobileTransport {
    var requests: [URLRequest] = []
    let status: Int
    let body: Data
    let url: URL?
    init(status: Int = 200, body: String = "{}", url: URL? = nil) {
        self.status = status; self.body = Data(body.utf8); self.url = url
    }
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        return (body, HTTPURLResponse(url: url ?? request.url!, statusCode: status, httpVersion: "HTTP/1.1", headerFields: [:])!)
    }
}

final class MobileTransportTests: XCTestCase {
    func testProductionURLSessionRefusesActualLoopbackHTTPRedirect() async throws {
        let server = try MobileRedirectServer()
        let port = try await server.start(); defer { server.stop() }
        let url = URL(string: "http://127.0.0.1:\(port)/start")!
        var request = URLRequest(url: url)
        request.setValue("Bearer synthetic-loopback-only", forHTTPHeaderField: "Authorization")
        let transport = MobileURLTransport()
        let (_, response) = try await transport.send(request)
        XCTAssertEqual(response.statusCode, 302)
        XCTAssertEqual(response.url, url)
        let requests = server.requests
        XCTAssertEqual(requests.count, 1, "The production session must never contact the redirect destination")
        XCTAssertTrue(requests.first?.contains("GET /start ") == true)
        XCTAssertTrue(requests.first?.contains("synthetic-loopback-only") == true)
        XCTAssertFalse(requests.contains { $0.contains("/destination") })
    }
    func testEndpointRequiresBareHTTPSOriginAndNormalizesDefaultPort() throws {
        XCTAssertEqual(try MobileEndpoint("https://AIR.example:443/").origin, "https://air.example")
        for value in ["http://air.example", "https://name:secret@air.example", "https://air.example/mobile", "https://air.example?token=x", "https://air.example#fragment", "https://air.example/%2F", "https://air.example:0"] {
            XCTAssertThrowsError(try MobileEndpoint(value))
        }
        let endpoint = try MobileEndpoint("https://air.example:8443")
        XCTAssertEqual(try endpoint.url(["memories"], cursor: "../a?private=yes").path, "/mobile/v1/memories")
        for id in ["../secret", "a/b", "", String(repeating: "a", count: 129), "中文"] {
            XCTAssertThrowsError(try endpoint.url(["memories", id]))
        }
    }
    func testPairingAndAuthenticatedRequestsUseContractBytesAndNoURLCredential() async throws {
        let wire = MobileWire(body: "{\"operation_id\":\"id\",\"kind\":\"mentor_message\",\"state\":\"completed\",\"result\":null,\"error\":null}")
        let endpoint = try MobileEndpoint("https://air.example")
        let api = MobileAPI(endpoint: endpoint, token: "example-synthetic-device-token", transport: wire)
        let receipt: MobileReceipt = try await api.request(["mentors", "future_self", "messages"], body: ["text": .string("中文 e\u{301}"), "operation_id": .string("id")])
        XCTAssertEqual(receipt.operationId, "id")
        let requests = await wire.requests
        let request = try XCTUnwrap(requests.first)
        XCTAssertEqual(request.url?.absoluteString, "https://air.example/mobile/v1/mentors/future_self/messages")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer example-synthetic-device-token")
        XCTAssertEqual(request.cachePolicy, .reloadIgnoringLocalAndRemoteCacheData)
        let body = try JSONDecoder().decode([String: MobileJSON].self, from: request.httpBody!)
        XCTAssertEqual(body["text"], .string("中文 e\u{301}")); XCTAssertEqual(body["operation_id"], .string("id"))
    }
    func testRedirectsAndForeignResponseOriginsNeverBecomeSuccessfulData() async throws {
        for (status, url) in [(302, URL(string: "https://air.example/mobile/v1/privacy")!), (200, URL(string: "https://other.example/mobile/v1/privacy")!)] {
            let api = MobileAPI(endpoint: try MobileEndpoint("https://air.example"), token: "example-synthetic", transport: MobileWire(status: status, url: url))
            do { let _: MobilePrivacy = try await api.request(["privacy"]); XCTFail("Must reject redirect/origin") }
            catch { XCTAssertEqual(error as? MobileError, .invalidResponse) }
        }
        let delegate = MobileRedirectBlocker(), session = URLSession(configuration: .ephemeral)
        let task = session.dataTask(with: URL(string: "https://air.example")!)
        let redirect = HTTPURLResponse(url: URL(string: "https://air.example")!, statusCode: 302, httpVersion: nil, headerFields: nil)!
        var forwarded = true
        delegate.urlSession(session, task: task, willPerformHTTPRedirection: redirect,
            newRequest: URLRequest(url: URL(string: "https://other.example")!)) { forwarded = $0 != nil }
        XCTAssertFalse(forwarded); task.cancel(); session.invalidateAndCancel()
    }
    func testUnauthorizedInvalidJSONAndUnsafeErrorsStayDistinctFromEmptySuccess() async throws {
        for (status, body, expected) in [(401, "{}", MobileError.unauthorized), (200, "{}", .invalidResponse), (503, "{\"detail\":\"https://secret.example/key\"}", .server(503, "unavailable"))] {
            let api = MobileAPI(endpoint: try MobileEndpoint("https://air.example"), token: "example-synthetic", transport: MobileWire(status: status, body: body))
            do { let _: MobileMemoryPage = try await api.request(["memories"]); XCTFail("Must reject error") }
            catch { XCTAssertEqual(error as? MobileError, expected); XCTAssertFalse(error.localizedDescription.contains("secret")) }
        }
    }
    func testOversizedMutationAndInvalidTokenNeverReachTransport() async throws {
        let wire = MobileWire(), endpoint = try MobileEndpoint("https://air.example")
        // Synthetic, deliberately malformed header; the CRLF must never reach transport.
        let invalidHeader = ["example-token", "Injected: yes"].joined(separator: "\r\n")
        for api in [MobileAPI(endpoint: endpoint, token: invalidHeader, transport: wire), MobileAPI(endpoint: endpoint, token: "valid", transport: wire)] {
            do { let _: MobileReceipt = try await api.request(["pair"], body: ["text": .string(String(repeating: "a", count: 70_000))]); XCTFail("Must reject input") }
            catch { XCTAssertEqual(error as? MobileError, .invalidInput) }
        }
        let requests = await wire.requests; XCTAssertTrue(requests.isEmpty)
    }
    func testFamilyEmojiEncodingIsValidatedBeforeTransport() async throws {
        let text = String(repeating: "👨‍👩‍👧‍👦", count: 3000)
        XCTAssertEqual(text.count, 3000)
        let wire = MobileWire(), api = MobileAPI(endpoint: try MobileEndpoint("https://air.example"), token: "example-synthetic", transport: wire)
        XCTAssertThrowsError(try api.preparedRequest(["mentors", "future_self", "messages"], body: ["text": .string(text)]))
        let requests = await wire.requests; XCTAssertTrue(requests.isEmpty)
    }
    func testConversationNoticesHaveSafeActionableCopy() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        for code in ["source_changed_or_revoked", "interrupted", "https://private.example/secret"] {
            let data = try JSONSerialization.data(withJSONObject: ["status": "interrupted", "revision": 1, "messages": [], "notice": code])
            let conversation = try decoder.decode(MobileConversation.self, from: data)
            XCTAssertNotNil(conversation.noticeText)
            XCTAssertFalse(conversation.noticeText!.contains("secret"))
            XCTAssertTrue(conversation.noticeText!.contains("核对"))
        }
    }
    func testRevokedSourceDirectsNewMessageAndDisablesResume() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        for code in ["source_revoked", "source_changed_or_revoked", "source_changed"] {
            let data = try JSONSerialization.data(withJSONObject: ["status": "interrupted", "revision": 1, "messages": [], "notice": code])
            let conversation = try decoder.decode(MobileConversation.self, from: data)
            XCTAssertFalse(conversation.resumable); XCTAssertTrue(conversation.needsNewMessage)
            XCTAssertTrue(conversation.noticeText!.contains("新消息"))
            XCTAssertTrue(conversation.noticeText!.contains("当前来源"))
        }
    }
    func testDecodedConversationAllowsNewMessagesOnlyInSupportedStates() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let cases: [(String, String?, Bool)] = [
            ("empty", nil, true), ("completed", nil, true), ("partial", nil, true), ("stopped", nil, true),
            ("interrupted", "source_revoked", true), ("interrupted", "source_changed_or_revoked", true),
            ("interrupted", "source_changed", true), ("interrupted", "model_refused", false),
            ("stopped", "model_refused", true), ("interrupted", nil, false), ("interrupted", "model_timeout", false),
            ("queued", nil, false), ("running", nil, false), ("delivering", nil, false),
            ("failed", nil, false), ("unknown", nil, false), ("future_status", "source_changed", false)
        ]
        for (status, notice, accepted) in cases {
            var object: [String: Any] = ["status": status, "revision": 1, "messages": []]
            object["notice"] = notice
            let conversation = try decoder.decode(MobileConversation.self, from: JSONSerialization.data(withJSONObject: object))
            XCTAssertEqual(conversation.acceptsNewMessage, accepted, status + ":" + (notice ?? "none"))
        }
    }
    func testModelRefusalExposesStopBeforeAdjustedMessageInsteadOfResume() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        for status in ["interrupted", "stopped"] {
            let data = try JSONSerialization.data(withJSONObject: ["status": status, "revision": 1, "messages": [], "notice": "model_refused"])
            let conversation = try decoder.decode(MobileConversation.self, from: data)
            XCTAssertEqual(conversation.canStop, status == "interrupted")
            XCTAssertEqual(conversation.requiresStopBeforeNewMessage, status == "interrupted")
            XCTAssertFalse(conversation.resumable)
            XCTAssertTrue(conversation.noticeText!.contains(status == "interrupted" ? "先点停止" : "已停止"))
        }
    }
    func testWorkerModelFailureCodesDistinguishUncertainResultsFromDefiniteRejections() throws {
        let uncertain = ["model_timeout", "model_connection_failed", "model_transport_failed", "model_server_failed", "model_output_invalid", "model_request_failed"]
        let definite = ["model_queue_timeout", "model_authentication_failed", "model_permission_denied", "model_rate_limited", "model_quota_exhausted", "model_request_rejected", "model_refused", "model_runtime_unavailable"]
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        for code in uncertain + definite {
            let data = try JSONSerialization.data(withJSONObject: ["status": "interrupted", "revision": 1, "messages": [], "notice": code])
            let conversation = try decoder.decode(MobileConversation.self, from: data)
            let text = try XCTUnwrap(conversation.noticeText)
            XCTAssertEqual(text.contains("结果尚未核实"), uncertain.contains(code), code)
            XCTAssertFalse(text.contains("本次回复未能完成"), "An established worker code should not use the fallback")
            XCTAssertFalse(text.contains("设备凭据"), "Model authentication is not phone authentication")
        }
    }
    func testUnavailableMessagesAndSourcePathsCannotExposeStaleTextOrEscapeVault() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let message = try decoder.decode(MobileMessage.self, from: Data("{\"id\":\"m\",\"actor\":\"future_self\",\"text\":\"stale private answer\",\"created_at\":1,\"unavailable\":\"source_changed_or_revoked\",\"source_ids\":[]}".utf8))
        XCTAssertFalse(message.visibleText.contains("private"))
        for path in ["../private.md", "/private.md", "a/%2e%2e/private.md", "https://host/a.md", "a\\b.md"] {
            let data = try JSONSerialization.data(withJSONObject: ["id": "source", "relative_path": path])
            XCTAssertNil(try decoder.decode(MobileSource.self, from: data).safeRelativePath)
        }
    }
}

/// A real TCP peer exercises URLSession's installed delegate, without TLS changes or real credentials.
private final class MobileRedirectServer: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "MobileRedirectServer")
    private let lock = NSLock()
    private var received: [String] = []
    var requests: [String] { lock.lock(); defer { lock.unlock() }; return received }
    init() throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        listener = try NWListener(using: parameters)
    }
    func start() async throws -> UInt16 {
        try await withCheckedThrowingContinuation { continuation in
            listener.stateUpdateHandler = { [weak self] state in
                guard let self else { return }
                if case .ready = state, let port = self.listener.port {
                    self.listener.stateUpdateHandler = nil; continuation.resume(returning: port.rawValue)
                } else if case .failed(let error) = state {
                    self.listener.stateUpdateHandler = nil; continuation.resume(throwing: error)
                }
            }
            listener.newConnectionHandler = { [weak self] connection in self?.receive(connection) }
            listener.start(queue: queue)
        }
    }
    private func receive(_ connection: NWConnection, buffered: Data = Data()) {
        if buffered.isEmpty { connection.start(queue: queue) }
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, complete, error in
            guard let self, let data, error == nil else { connection.cancel(); return }
            let accumulated = buffered + data
            let text = String(decoding: accumulated, as: UTF8.self)
            guard text.contains("\r\n\r\n") else {
                if !complete, accumulated.count < 65_536 { self.receive(connection, buffered: accumulated) }
                else { connection.cancel() }
                return
            }
            self.lock.lock(); self.received.append(text); self.lock.unlock()
            let port = self.listener.port!.rawValue
            let response = text.hasPrefix("GET /start ")
                ? "HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:\(port)/destination\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                : "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in connection.cancel() })
        }
    }
    func stop() { listener.cancel() }
}
