#if DEBUG
import Foundation
import RijiCore

/// Explicit synthetic UI namespace only. No model, network, or real diary is accessed.
actor MobileRoundtableFixture {
    private let file: URL
    private let seedLegacyHistory: Bool
    private var records: [String: [String: Any]] = [:]
    private var receipts: [String: [String: Any]] = [:]
    private var results: [String: [String: Any]] = [:]
    private var currentID: String?
    private let mentors = ["gentle_reviewer", "blunt_coach", "future_self", "wang_yangming"]
    init(file: URL, seedLegacyHistory: Bool = false) {
        self.file = file
        self.seedLegacyHistory = seedLegacyHistory
        if let data = try? Data(contentsOf: file), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            records = object["records"] as? [String: [String: Any]] ?? [:]
            receipts = object["receipts"] as? [String: [String: Any]] ?? [:]
            results = object["results"] as? [String: [String: Any]] ?? [:]
            currentID = object["current"] as? String
        }
    }
    func response(_ request: URLRequest, memories: [[String: Any]] = []) throws -> (Data, HTTPURLResponse)? {
        guard let url = request.url else { return nil }
        let parts = url.pathComponents
        guard parts.count >= 4 else { return nil }
        if parts[3] == "operations", let receipt = receipts[url.lastPathComponent] { return try encode(receipt, url: url) }
        guard parts[3] == "roundtables" else { return nil }
        let body = request.httpBody.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
        if let op = body["operation_id"] as? String, let receipt = receipts[op] { return try encode(receipt, url: url) }
        if parts.count == 4, request.httpMethod == "GET" {
            // Existing legacy history keeps compatibility paths testable without a new-host entry.
            if seedLegacyHistory && records.isEmpty {
                let identifier = "00000000-0000-4000-8000-000000000056"
                var legacy = base(identifier)
                append("已有的合成圆桌记录", actor: "user", kind: "user", value: &legacy)
                append("这是保留的旧圆桌，可以继续查看。", actor: "host", kind: "followup", value: &legacy)
                records[identifier] = legacy
                currentID = identifier
                try save()
            }
            let items = records.values.map { value -> [String: Any] in
                let messages = value["messages"] as? [[String: Any]] ?? []
                return ["id": value["conversation_id"]!, "title": value["title"] ?? messages.first?["text"] ?? "圆桌", "status": value["status"]!, "updated_at": value["updated_at"] ?? Date().timeIntervalSince1970, "revision": value["revision"]!, "chat_kind": value["chat_kind"] ?? "legacy", "personas": value["personas"] ?? []]
            }
            return try encode(["items": items, "current_id": currentID as Any? ?? NSNull(), "truncated": false], url: url)
        }
        if parts.count == 4 {
            let id = UUID().uuidString
            var value = base(id)
            if body["chat_kind"] as? String == "group" {
                let members = body["personas"] as? [String] ?? []
                let names = ["gentle_reviewer": "温柔回顾者", "blunt_coach": "直率教练", "future_self": "未来的我", "wang_yangming": "王阳明"]
                value["chat_kind"] = "group"; value["personas"] = members; value["audience"] = members
                value["title"] = members.map { names[$0] ?? "导师" }.joined(separator: "、")
                value["run_personas"] = []; value["run_kind"] = "group"
            } else {
                append(body["text"] as? String ?? "我想每周留出两小时学习。", actor: "user", kind: "user", value: &value, origin: body["statement_kind"] as? String ?? "user_statement")
                append("可以先把目标缩成一个两周实验。你最希望改善什么？普通发送只由我接话，需要比较不同意见时再邀请导师。", actor: "host", kind: "followup", value: &value)
            }
            records[id] = value; currentID = id
            return try receipt(body, kind: "roundtable_create", result: ["conversation_id": id], url: url)
        }
        let id = parts[4]
        guard var value = records[id] else { return try encode(["detail": "roundtable_not_found"], url: url, status: 404) }
        if parts.count == 5 {
            if value["status"] as? String == "running" {
                value["status"] = "completed"
                append("共同建议：先试两周，再依据实际执行反馈调整。分歧在于时间安排：固定时段更稳定，弹性时间更容易开始。前提是每周至少有一个不受打扰的时段。下一步：选一个最小行动，记录是否执行。", actor: "host", kind: "comparison", value: &value)
                records[id] = value; try save()
            }
            value["history_version"] = SourceHash.digest(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]))
            value["cache_expires_at"] = Date().timeIntervalSince1970 + 900
            return try encode(value, url: url)
        }
        switch parts[5] {
        case "messages":
            value["revision"] = (value["revision"] as? Int ?? 1) + 1
            append(body["text"] as? String ?? "", actor: "user", kind: "user", value: &value, origin: body["statement_kind"] as? String ?? "user_statement")
            if value["chat_kind"] as? String == "group" {
                value["run_id"] = UUID().uuidString
                let requested = body["reply_personas"] as? [String] ?? []
                let responders = requested.isEmpty ? value["personas"] as? [String] ?? [] : requested
                value["run_personas"] = responders
                for actor in responders { append("这是一段合成测试回复：先选一个可以验证的小行动，再根据实际反馈调整。", actor: actor, kind: "opinion", value: &value) }
            } else { append("收到这次补充。我会把它作为当前条件；建议与实际反馈分别保留。如果条件改变了，可以先总结或纠正旧背景。", actor: "host", kind: "followup", value: &value) }
            value["status"] = "completed"; records[id] = value
            return try receipt(body, kind: "roundtable_message", result: ["conversation_id": id], url: url)
        case "materials":
            let selected = body["memories"] as? [[String: String]] ?? []
            let chosen = selected.compactMap { choice in memories.first { $0["id"] as? String == choice["id"] && $0["version"] as? String == choice["expected_version"] } }
            return try encode(["preview_hash": String(repeating: "a", count: 64), "artifact_ids": body["artifact_ids"] ?? [], "memories": chosen, "audience": ["host"] + mentors, "personas": body["personas"] ?? [], "mode": body["mode"] ?? "reference", "rounds": body["rounds"] ?? 1, "revision": value["revision"]!], url: url)
        case "commands":
            let action = body["action"] as? String ?? ""
            value["revision"] = (value["revision"] as? Int ?? 1) + 1
            if action == "rename" { value["title"] = body["text"] as? String ?? "" }
            else if action == "start_run" {
                value["run_id"] = UUID().uuidString; value["run_kind"] = "roundtable"; value["status"] = "running"
                value["mode"] = body["mode"] ?? "reference"; value["rounds"] = body["rounds"] ?? 1
                value["run_personas"] = body["personas"] ?? []
                for actor in body["personas"] as? [String] ?? [] {
                    let advice = ["gentle_reviewer": "建议先降低起步难度，每周做一个小练习，避免把学习变成额外压力。", "blunt_coach": "如果不明确开始时间，很容易继续拖延。先在日历里固定一个时段，再检查真实执行。", "future_self": "长期看，留下每周可复用的成果比累计时长更有价值。", "wang_yangming": "把理解落实在一次具体行动中，再用反馈校正认识。"]
                    append(advice[actor] ?? "先试一次再看反馈。", actor: actor, kind: "opinion", value: &value)
                }
            } else if action == "stop" { value["status"] = "stopped" }
            else if action == "archive" { value["status"] = "archived" }
            else if action == "restore" { value["status"] = "stopped" }
            else if action == "correct" {
                value["correction_version"] = (value["correction_version"] as? Int ?? 0) + 1
                var messages = value["messages"] as? [[String: Any]] ?? []
                let selected = Set(body["supersedes"] as? [String] ?? [])
                for index in messages.indices where messages[index]["actor"] as? String != "user" || selected.contains(messages[index]["id"] as? String ?? "") { messages[index]["superseded"] = true }
                value["messages"] = messages
                append(body["text"] as? String ?? "条件已改变", actor: "user", kind: "user", value: &value)
                value["status"] = "waiting_user"
            } else {
                value["status"] = "completed"
                append(action == "summarize" ? "部分总结：已完成的建议供参考，未完成视角仍缺失。请结合新的条件再决定下一步。" : "我们按最新条件继续。先选一个可验证的小行动，再回来记录实际反馈。", actor: "host", kind: "synthesis", value: &value)
            }
            records[id] = value
            return try receipt(body, kind: "roundtable_command", result: ["conversation_id": id], url: url)
        case "result-preview":
            let chosen = Set(body["artifact_ids"] as? [String] ?? [])
            let messages = (value["messages"] as? [[String: Any]] ?? []).filter { chosen.contains($0["id"] as? String ?? "") }
            let resultID = UUID().uuidString
            let provenance: [String: Any] = ["result_id": resultID, "problem_id": id, "discussion_id": value["run_id"]!, "artifact_ids": Array(chosen), "input_revision": value["revision"]!, "summary_version": 1, "correction_version": value["correction_version"]!, "recorded_at": ISO8601DateFormatter().string(from: Date()), "mentors": value["run_personas"] as? [String] ?? ["host"], "topic": "两周学习实验", "saved_date": "", "edited_by_user": false]
            results[resultID] = ["result_id": resultID, "text": messages.compactMap { $0["text"] as? String }.joined(separator: "\n\n"), "provenance": provenance, "binding": String(repeating: "b", count: 64)]
            return try receipt(body, kind: "roundtable_result", result: ["result_id": resultID], url: url)
        case "result-validation":
            guard var result = results[body["result_id"] as? String ?? ""], let provenance = result["provenance"] as? [String: Any], provenance["correction_version"] as? Int == value["correction_version"] as? Int else {
                return try encode(["detail": "handoff_source_unavailable"], url: url, status: 409)
            }
            result["validated_at"] = Date().timeIntervalSince1970
            return try encode(result, url: url)
        default: return try encode(["detail": "not_found"], url: url, status: 404)
        }
    }
    private func base(_ id: String) -> [String: Any] {
        ["conversation_id": id, "status": "completed", "revision": 1, "state_revision": 1, "summary_version": 1, "correction_version": 0, "run_id": UUID().uuidString, "run_kind": "followup", "run_personas": ["host"], "mode": "reference", "rounds": 1, "suspended_run_id": "", "messages": [], "current_summary": NSNull(), "runs": [], "audience": ["host"] + mentors, "notice": NSNull(), "selected_artifact_ids": [], "next_cursor": NSNull()]
    }
    private func append(_ text: String, actor: String, kind: String, value: inout [String: Any], origin: String = "user_statement") {
        var messages = value["messages"] as? [[String: Any]] ?? []
        messages.append(["id": UUID().uuidString, "actor": actor, "kind": kind, "text": text, "created_at": Date().timeIntervalSince1970, "input_revision": value["revision"]!, "run_id": value["run_id"]!, "origin_kind": actor == "user" ? origin : "ai_discussion", "superseded": false, "unavailable": NSNull(), "uncertainties": [], "next_steps": [], "source_ids": []])
        value["messages"] = messages; value["updated_at"] = Date().timeIntervalSince1970
    }
    private func receipt(_ body: [String: Any], kind: String, result: [String: Any], url: URL) throws -> (Data, HTTPURLResponse) {
        let id = body["operation_id"] as? String ?? UUID().uuidString
        let receipt: [String: Any] = ["operation_id": id, "kind": kind, "state": "completed", "result": result, "error": NSNull()]
        receipts[id] = receipt; try save(); return try encode(receipt, url: url)
    }
    private func save() throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: ["records": records, "receipts": receipts, "results": results, "current": currentID as Any? ?? NSNull()]).write(to: file, options: .atomic)
    }
    private func encode(_ value: [String: Any], url: URL, status: Int = 200) throws -> (Data, HTTPURLResponse) {
        (try JSONSerialization.data(withJSONObject: value), HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
}
#endif

#if DEBUG && targetEnvironment(simulator)
import UIKit
import ObjectiveC.runtime
import OSLog

// Temporary synthetic-only menu-delivery diagnosis; never enabled by normal launches.
@MainActor enum SyntheticTouchTrace {
    enum Action: String, Sendable {
        case armed, multiselectEnter, multiselectReturn, selectionChanged, selectingChanged
        case viewAppeared, viewDisappeared, inboxBegin, inboxEnd
        case inboxCapabilitiesPublished, inboxListPublished, roundtableRefreshBegin, roundtableRefreshEnd
        case foregroundTickBegin, foregroundTickEnd, incompleteTouch, stopped, capacityReached
    }
    private struct Gesture: Encodable, Sendable {
        let name: String
        let state: Int
        let enabled: Bool
    }
    private struct Ancestor: Encodable, Sendable {
        let kind = "ancestor"
        let sample: Int
        let depth: Int
        let name: String
        let frame: [Double]
        let recognizerCount: Int
        let recognizers: [Gesture]
    }
    private struct TouchMetadata: Encodable, Sendable {
        let kind = "touch"
        let sample: Int
        let touch: Int
        let stage: String
        let eventTime: Double
        let touchTime: Double
        let handledTime: Double
        let phase: Int
        let point: [Double]
        let window: String
        let windowFrame: [Double]
        let windowLevel: Double
        let keyWindow: Bool
        let target: String
        let ancestorCount: Int
    }
    private struct ActionMetadata: Encodable, Sendable {
        let kind = "action"
        let action: String
        let time: Double
        let busy: Bool?
        let selecting: Bool?
        let count: Int?
        let current: Bool?
        let available: Bool?
    }
    private enum Entry: Sendable {
        case touch(TouchMetadata), ancestor(Ancestor), action(ActionMetadata)
    }
    struct PendingTouch {
        let touch: UITouch
        let sequence: Int
        let eventTime: Double
        let ancestors: [UIView]
    }
    private static var installed = false
    private static var didArm = false
    private static var recording = false
    private static var deadline = 0.0
    private static var touches: [ObjectIdentifier: Int] = [:]
    private static var touchSequence = 0
    private static var sampleSequence = 0
    private static var accepted = 0
    private static var entries: [Entry] = []
    private static var flushScheduled = false
    private static var watchdogScheduled = false
    private static var observationEndScheduled = false
    private static let output = DispatchQueue(label: "riji.synthetic-menu-delivery")

    static func installIfRequested() {
        let environment = ProcessInfo.processInfo.environment
        guard !installed,
              environment["RIJI_TEST_TOUCH_TRACE"] == "host-invitation-multiselect",
              environment["RIJI_TEST_MOBILE"] == "roundtable",
              environment["RIJI_TEST_CONTAINER"].flatMap(UUID.init(uuidString:)) != nil,
              environment["RIJI_TEST_ORIGINAL_VAULT"] == nil,
              let original = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.sendEvent(_:))),
              let trace = class_getInstanceMethod(UIWindow.self, #selector(UIWindow.riji_traceSyntheticMenu(_:))) else { return }
        method_exchangeImplementations(original, trace)
        installed = true
    }

    static func arm() {
        guard installed, !didArm else { return }
        didArm = true; recording = true
        deadline = ProcessInfo.processInfo.systemUptime + 120
        mark(.armed)
        DispatchQueue.main.asyncAfter(deadline: .now() + 120) { stop() }
    }

    static func mark(_ action: Action, busy: Bool? = nil, selecting: Bool? = nil,
                     count: Int? = nil, current: Bool? = nil, available: Bool? = nil) {
        guard isRecording else { return }
        append(.action(ActionMetadata(action: action.rawValue, time: ProcessInfo.processInfo.systemUptime,
            busy: busy, selecting: selecting, count: count, current: current, available: available)))
        scheduleFlush()
    }

    static func finishSelectionObservation() {
        guard isRecording, !observationEndScheduled else { return }
        observationEndScheduled = true
        // Observe one existing inbox period after selection without changing its schedule.
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) { stop() }
    }

    private static func stop() {
        guard recording else { return }
        append(.action(ActionMetadata(action: Action.stopped.rawValue,
            time: ProcessInfo.processInfo.systemUptime, busy: nil, selecting: nil,
            count: touches.count, current: nil, available: nil)))
        recording = false
        scheduleFlush()
    }

    private static var isRecording: Bool {
        recording && ProcessInfo.processInfo.systemUptime <= deadline && accepted < 256
    }

    static func capture(_ event: UIEvent, in window: UIWindow) -> [PendingTouch] {
        guard event.type == .touches, isRecording else { return [] }
        var pending: [PendingTouch] = []
        for touch in (event.allTouches ?? []).prefix(4) where touch.window === window {
            guard [.began, .ended, .cancelled].contains(touch.phase) else { continue }
            let identity = ObjectIdentifier(touch)
            if touch.phase == .began {
                guard touches.count < 4 else { continue }
                touchSequence += 1; touches[identity] = touchSequence
            }
            guard let sequence = touches[identity] else { continue }
            var ancestors: [UIView] = []
            var view = touch.view
            while let current = view, ancestors.count < 24 {
                ancestors.append(current); view = current.superview
            }
            let record = PendingTouch(touch: touch, sequence: sequence, eventTime: event.timestamp, ancestors: ancestors)
            captureMetadata(record, in: window, stage: "before")
            pending.append(record)
        }
        return pending
    }

    static func finish(_ pending: [PendingTouch], in window: UIWindow) {
        for record in pending {
            captureMetadata(record, in: window, stage: "after")
            if [.ended, .cancelled].contains(record.touch.phase) {
                touches.removeValue(forKey: ObjectIdentifier(record.touch))
            }
        }
        scheduleFlush()
        scheduleWatchdog()
    }

    private static func captureMetadata(_ record: PendingTouch, in window: UIWindow, stage: String) {
        guard isRecording else { return }
        sampleSequence += 1
        let sample = sampleSequence, point = record.touch.location(in: window)
        append(.touch(TouchMetadata(sample: sample, touch: record.sequence, stage: stage,
            eventTime: record.eventTime, touchTime: record.touch.timestamp, handledTime: ProcessInfo.processInfo.systemUptime,
            phase: record.touch.phase.rawValue, point: [Double(point.x), Double(point.y)],
            window: className(window), windowFrame: rect(window.frame), windowLevel: Double(window.windowLevel.rawValue),
            keyWindow: window.isKeyWindow, target: record.touch.view.map(className) ?? "none", ancestorCount: record.ancestors.count)))
        for (depth, view) in record.ancestors.enumerated() {
            let recognizers = view.gestureRecognizers ?? []
            guard !recognizers.isEmpty else { continue }
            append(.ancestor(Ancestor(sample: sample, depth: depth, name: className(view),
                frame: rect(view.convert(view.bounds, to: window)), recognizerCount: recognizers.count,
                recognizers: recognizers.prefix(8).map { Gesture(name: className($0), state: $0.state.rawValue, enabled: $0.isEnabled) })))
        }
    }

    private static func className(_ object: NSObject) -> String {
        String(NSStringFromClass(type(of: object)).prefix(48))
    }

    private static func rect(_ value: CGRect) -> [Double] {
        [Double(value.minX), Double(value.minY), Double(value.width), Double(value.height)]
    }

    private static func append(_ entry: Entry) {
        guard accepted < 256 else { return }
        accepted += 1
        if accepted == 256 {
            entries.append(.action(ActionMetadata(action: Action.capacityReached.rawValue,
                time: ProcessInfo.processInfo.systemUptime, busy: nil, selecting: nil,
                count: accepted, current: nil, available: nil)))
            recording = false
        } else { entries.append(entry) }
    }

    private static func scheduleWatchdog() {
        guard isRecording, !watchdogScheduled, !touches.isEmpty else { return }
        watchdogScheduled = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            watchdogScheduled = false
            guard isRecording, !touches.isEmpty else { return }
            mark(.incompleteTouch, count: touches.count)
        }
    }

    private static func scheduleFlush() {
        guard !flushScheduled, !entries.isEmpty else { return }
        flushScheduled = true
        DispatchQueue.main.async {
            flushScheduled = false
            let batch = entries
            entries.removeAll(keepingCapacity: true)
            output.async {
                let logger = Logger(subsystem: "com.riji.ci-probe", category: "host-menu-delivery")
                for entry in batch {
                    let encoder = JSONEncoder()
                    encoder.nonConformingFloatEncodingStrategy = .convertToString(positiveInfinity: "infinity", negativeInfinity: "negativeInfinity", nan: "nan")
                    let data: Data?
                    switch entry {
                    case .touch(let value): data = try? encoder.encode(value)
                    case .ancestor(let value): data = try? encoder.encode(value)
                    case .action(let value): data = try? encoder.encode(value)
                    }
                    let text = data.flatMap { String(data: $0, encoding: .utf8) } ?? "{\"kind\":\"encodingFailed\"}"
                    logger.notice("\(text, privacy: .public)")
                }
            }
        }
    }
}

private extension UIWindow {
    @objc func riji_traceSyntheticMenu(_ event: UIEvent) {
        let pending = SyntheticTouchTrace.capture(event, in: self)
        // The exchanged selector calls the original implementation exactly once.
        riji_traceSyntheticMenu(event)
        SyntheticTouchTrace.finish(pending, in: self)
    }
}
#endif
