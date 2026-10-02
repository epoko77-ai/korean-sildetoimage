// Local, uncorrected OCR observations. Accepts one or more PNG/output-JSON pairs.
import Foundation
import Vision
import ImageIO
import CryptoKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("ERROR: " + message + "\n").utf8))
    exit(1)
}

guard CommandLine.arguments.count >= 3, CommandLine.arguments.count % 2 == 1 else {
    fail("Usage: swift ocr_macos.swift normalized.png output.json [normalized.png output.json ...]")
}
for argumentIndex in stride(from: 1, to: CommandLine.arguments.count, by: 2) {
let input = URL(fileURLWithPath: CommandLine.arguments[argumentIndex]).standardizedFileURL
let output = URL(fileURLWithPath: CommandLine.arguments[argumentIndex + 1]).standardizedFileURL
guard input.pathExtension.lowercased() == "png" else { fail("Use a normalized PNG") }
guard !FileManager.default.fileExists(atPath: output.path) else { fail("Output already exists") }
guard let imageData = try? Data(contentsOf: input),
      let source = CGImageSourceCreateWithData(imageData as CFData, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("Cannot read input image") }
let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
if let orientation = properties?[kCGImagePropertyOrientation] as? Int, orientation != 1 {
    fail("Normalize image orientation before OCR")
}

do {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0
    let languages = ["ko-KR", "en-US"]
    let supported = try request.supportedRecognitionLanguages()
    guard languages.allSatisfy({ supported.contains($0) }) else {
        fail("Required languages unavailable; supported: \(supported)")
    }
    request.recognitionLanguages = languages
    let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
    try handler.perform([request])
    let width = Double(image.width), height = Double(image.height)
    let observations: [[String: Any]] = (request.results ?? []).enumerated().map { index, result in
        let rect = result.boundingBox
        let candidates = result.topCandidates(3)
        // Character boxes are localization hints, not exact ink masks. Keep raw text unchanged.
        var characters: [[String: Any]] = []
        if let best = candidates.first {
            let string = best.string
            var cursor = string.startIndex
            var offset = 0
            while cursor < string.endIndex {
                let next = string.index(after: cursor)
                var character: [String: Any] = ["index": offset, "text": String(string[cursor..<next])]
                if let region = try? best.boundingBox(for: cursor..<next) {
                    let bounds = region.boundingBox
                    character["bbox"] = [max(0, Int(floor(bounds.minX * width))),
                                         max(0, Int(floor((1 - bounds.maxY) * height))),
                                         min(image.width, Int(ceil(bounds.maxX * width))),
                                         min(image.height, Int(ceil((1 - bounds.minY) * height)))]
                }
                characters.append(character)
                cursor = next
                offset += 1
            }
        }
        return [
            "observation_id": String(format: "ocr-%04d", index + 1),
            "text": candidates.first?.string ?? "",
            "confidence": Double(candidates.first?.confidence ?? 0),
            "characters": characters,
            "bbox": [max(0, Int(floor(rect.minX * width))),
                     max(0, Int(floor((1 - rect.maxY) * height))),
                     min(image.width, Int(ceil(rect.maxX * width))),
                     min(image.height, Int(ceil((1 - rect.minY) * height)))],
            "alternatives": candidates.dropFirst().map { ["text": $0.string, "confidence": Double($0.confidence)] as [String: Any] }
        ]
    }
    let report: [String: Any] = [
        "schema_version": 1, "engine": "Apple Vision", "revision": request.revision,
        "image": input.path,
        "image_sha256": SHA256.hash(data: imageData).map { String(format: "%02x", $0) }.joined(),
        "width": image.width, "height": image.height,
        "languages": languages, "language_correction": false,
        "coordinate_system": "top-left pixel xyxy; right/bottom exclusive",
        "reading_order_verified": false, "observations": observations
    ]
    try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: output, options: [.withoutOverwriting])
    print("OCR observations: \(observations.count); output: \(output.path)")
} catch {
    fail(String(describing: error))
}
}
