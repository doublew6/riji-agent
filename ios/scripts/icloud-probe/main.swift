import Foundation
import Darwin

func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardOutput.write(Data((text + "\n").utf8))
}

struct PollTimeout: Error { let lastCode: String }

/// The watchdog owns only read-only polling. It can end a blocked coordinator;
/// create/mac-step never construct it. DispatchTime is monotonic.
final class PollBudget {
    private let deadline: DispatchTime
    private let lock = NSLock()
    private let timer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "riji.probe.read-deadline"))
    private var active = true
    private var lastCode = "noCompletedAttempt"

    init(seconds: Int) {
        deadline = .now() + .seconds(seconds)
        timer.schedule(deadline: deadline)
        timer.setEventHandler { [weak self] in self?.expire() }
        timer.resume()
    }

    var remaining: TimeInterval {
        let now = DispatchTime.now().uptimeNanoseconds
        return deadline.uptimeNanoseconds > now ? Double(deadline.uptimeNanoseconds - now) / 1_000_000_000 : 0
    }

    func note(_ error: Error) { lock.lock(); defer { lock.unlock() }; lastCode = ProbeFailure.code(error) }
    func timeout() -> PollTimeout { lock.lock(); defer { lock.unlock() }; return PollTimeout(lastCode: lastCode) }
    func cancel() { lock.lock(); active = false; timer.cancel(); lock.unlock() }

    private func expire() {
        lock.lock()
        guard active else { lock.unlock(); return }
        active = false
        emit(["status": "failed", "code": "deadline", "lastCode": lastCode])
        _exit(124)
    }
}

func inspect(_ fixture: ProbeFixture, root: URL, phase: ProbeFixture.Phase, seconds: Int) throws {
    let budget = seconds > 0 ? PollBudget(seconds: seconds) : nil
    defer { budget?.cancel() }
    repeat {
        do {
            try fixture.validate(root, phase: phase)
            if let budget, budget.remaining == 0 { throw budget.timeout() }
            budget?.cancel()
            emit(["status": "exact-iCloud-bytes", "phase": phase.rawValue,
                  "run": fixture.run.uuidString, "hashes": fixture.hashes(phase)])
            return
        } catch {
            if error is PollTimeout { throw error }
            guard let budget else { throw error }
            budget.note(error)
            if ["wrongFixture", "unsafePath", "osConflict"].contains(ProbeFailure.code(error)) { throw error }
            guard budget.remaining > 0 else { throw budget.timeout() }
            Thread.sleep(forTimeInterval: min(1, budget.remaining))
        }
    } while true
}

func selfTest() throws {
    let base = FileManager.default.temporaryDirectory.resolvingSymlinksInPath().appendingPathComponent("RijiProbeCLI-\(UUID())")
    try FileManager.default.createDirectory(at: base, withIntermediateDirectories: false)
    defer { try? FileManager.default.removeItem(at: base) }
    let fixture = ProbeFixture(run: UUID()), root = base.appendingPathComponent(fixture.folderName)
    try fixture.create(at: root, storage: .localTest)
    try fixture.validate(root, phase: .pristine, storage: .localTest)
    try ProbeFixture.coordinate(root, writing: true) { url in
        try fixture.files(.phoneSaved)[ProbeFixture.savedPath]!.write(to: url.appendingPathComponent(ProbeFixture.savedPath), options: .atomic)
    }
    try fixture.writePhoneReceipt(root, storage: .localTest)
    try fixture.applyMacChanges(root, evidence: base.appendingPathComponent("evidence"), storage: .localTest)
    try fixture.validate(root, phase: .macReady, storage: .localTest)
    emit(["status": "local-protocol-self-test-passed", "physical": "not-executed", "cloud": "not-executed"])
}

func main() throws {
    let arguments = Array(CommandLine.arguments.dropFirst())
    if arguments == ["self-test-blocked-read"] {
        let budget = PollBudget(seconds: 1)
        withExtendedLifetime(budget) { DispatchSemaphore(value: 0).wait() }
        return
    }
    if arguments == ["self-test"] { try selfTest(); return }
    guard arguments.count >= 3, let run = UUID(uuidString: arguments[2]) else { throw ProbeFailure.invalidArguments }
    let fixture = ProbeFixture(run: run), root = URL(fileURLWithPath: arguments[1]).standardizedFileURL
    switch arguments[0] {
    case "create":
        guard arguments.count == 3 else { throw ProbeFailure.invalidArguments }
        try fixture.create(at: root)
        emit(["status": "synthetic-fixture-created", "run": run.uuidString, "hashes": fixture.hashes(.pristine)])
    case "inspect", "poll":
        let polling = arguments[0] == "poll"
        guard arguments.count == (polling ? 5 : 4), let phase = ProbeFixture.Phase(rawValue: arguments[3]) else { throw ProbeFailure.invalidArguments }
        let seconds = polling ? Int(arguments[4]) : 0
        guard let seconds, (polling ? 1...300 : 0...0).contains(seconds) else { throw ProbeFailure.invalidArguments }
        try inspect(fixture, root: root, phase: phase, seconds: seconds)
    case "mac-step":
        guard arguments.count == 4 else { throw ProbeFailure.invalidArguments }
        try fixture.applyMacChanges(root, evidence: URL(fileURLWithPath: arguments[3]).standardizedFileURL)
        emit(["status": "mac-phase-written-locally", "propagation": "pending-phone-verification",
              "run": run.uuidString, "hashes": fixture.hashes(.macReady)])
    default: throw ProbeFailure.invalidArguments
    }
}

do { try main() }
catch let error as PollTimeout { emit(["status": "failed", "code": "deadline", "lastCode": error.lastCode]); exit(124) }
catch { emit(["status": "failed", "code": ProbeFailure.code(error)]); exit(1) }
