import Foundation

public struct MobileEndpoint: Codable, Equatable, Sendable {
    public let origin: String
    public init(_ input: String) throws {
        guard var parts = URLComponents(string: input.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              ["", "/"].contains(parts.percentEncodedPath), parts.port.map({ (1...65535).contains($0) }) ?? true else { throw MobileError.invalidEndpoint }
        parts.scheme = "https"; parts.host = host.lowercased(); parts.path = ""
        if parts.port == 443 { parts.port = nil }
        guard let url = parts.url, url.host != nil else { throw MobileError.invalidEndpoint }
        origin = url.absoluteString
    }
    public func url(_ path: [String], cursor: String? = nil) throws -> URL {
        guard !path.isEmpty, path.allSatisfy(Self.validIdentifier),
              var parts = URLComponents(string: origin) else { throw MobileError.invalidInput }
        parts.path = "/mobile/v1/" + path.joined(separator: "/")
        parts.queryItems = cursor.map { [URLQueryItem(name: "cursor", value: $0)] }
        guard let url = parts.url else { throw MobileError.invalidEndpoint }
        return url
    }
    public static func validIdentifier(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 128 && value.utf8.allSatisfy {
            (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95
        }
    }
}

public protocol MobileTransport: Sendable {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct MobilePreparedRequest: Sendable {
    fileprivate let request: URLRequest
}

/// URLSession keeps normal system TLS validation; no credential-bearing redirects.
final class MobileRedirectBlocker: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

public final class MobileURLTransport: MobileTransport, @unchecked Sendable {
    private let session: URLSession
    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.urlCredentialStorage = nil; config.httpCookieStorage = nil; config.httpShouldSetCookies = false
        config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        config.timeoutIntervalForRequest = 30; config.timeoutIntervalForResource = 40
        session = URLSession(configuration: config, delegate: MobileRedirectBlocker(), delegateQueue: nil)
    }
    deinit { session.invalidateAndCancel() }
    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw MobileError.invalidResponse }
        return (data, response)
    }
}

public struct MobileAPI: Sendable {
    public let endpoint: MobileEndpoint
    private let token: String?
    private let transport: any MobileTransport
    public init(endpoint: MobileEndpoint, token: String?, transport: any MobileTransport) {
        self.endpoint = endpoint; self.token = token; self.transport = transport
    }
    public func preparedRequest(_ path: [String], body: [String: MobileJSON]? = nil, cursor: String? = nil) throws -> MobilePreparedRequest {
        let url = try endpoint.url(path, cursor: cursor)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData)
        request.httpMethod = body == nil ? "GET" : "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token {
            guard !token.isEmpty, token.unicodeScalars.allSatisfy({ (33...126).contains($0.value) }), token.utf8.count <= 4096 else { throw MobileError.invalidInput }
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        if let body {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            do { request.httpBody = try encoder.encode(body) } catch { throw MobileError.invalidInput }
            guard request.httpBody!.count <= 65_536 else { throw MobileError.invalidInput }
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return MobilePreparedRequest(request: request)
    }
    public func request<T: Decodable>(_ path: [String], body: [String: MobileJSON]? = nil, cursor: String? = nil) async throws -> T {
        try await send(preparedRequest(path, body: body, cursor: cursor))
    }
    public func send<T: Decodable>(_ prepared: MobilePreparedRequest) async throws -> T {
        let request = prepared.request
        guard let url = request.url else { throw MobileError.invalidInput }
        let data: Data, response: HTTPURLResponse
        do { (data, response) = try await transport.send(request) }
        catch is CancellationError { throw CancellationError() }
        catch { throw MobileError.transport }
        guard response.url == url, !(300..<400).contains(response.statusCode), data.count <= 4_194_304 else { throw MobileError.invalidResponse }
        if response.statusCode == 401 { throw MobileError.unauthorized }
        guard (200..<300).contains(response.statusCode) else {
            let object = try? JSONDecoder().decode([String: String].self, from: data)
            let code = object?["detail"] ?? "unavailable"
            throw MobileError.server(response.statusCode, MobileEndpoint.validIdentifier(code) ? code : "unavailable")
        }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        do { return try decoder.decode(T.self, from: data) } catch { throw MobileError.invalidResponse }
    }
}
