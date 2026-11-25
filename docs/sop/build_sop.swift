// macOS-only documentation build/QA. No application or model dependencies.
import AppKit
import CoreText
import PDFKit
import Foundation

enum BuildError: Error, CustomStringConvertible {
    case message(String)
    var description: String { if case let .message(s) = self { return s }; return "build error" }
}
let args = CommandLine.arguments
guard args.count == 4 else {
    fputs("Usage: swift build_sop.swift SOURCE.md OUTPUT.pdf QA_DIRECTORY\n", stderr); exit(2)
}
let sourceURL = URL(fileURLWithPath: args[1]).standardizedFileURL
let outputURL = URL(fileURLWithPath: args[2]).standardizedFileURL
let qaURL = URL(fileURLWithPath: args[3]).standardizedFileURL
let fm = FileManager.default
let pageWidth: CGFloat = 595.276, pageHeight: CGFloat = 841.89
let margin: CGFloat = 46, bodyWidth: CGFloat = 503.276
let regularName = "PingFangSC-Regular", boldName = "PingFangSC-Semibold"
guard NSFont(name: regularName, size: 12) != nil, NSFont(name: boldName, size: 12) != nil else {
    fputs("Required macOS PingFang SC fonts unavailable; no fallback PDF generated.\n", stderr); exit(3)
}
func color(_ hex: UInt32) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 255)/255, green: CGFloat((hex >> 8) & 255)/255,
            blue: CGFloat(hex & 255)/255, alpha: 1)
}
let ink = color(0x203344), accent = color(0x12648E), pale = color(0xEDF5FA)

struct TextBox: Codable { let page: Int; let top, height, width: Double; let text: String }
final class Painter {
    let context: CGContext
    var page = 0, top: CGFloat = 54
    var boxes = [TextBox]()
    init(_ c: CGContext) { context = c }
    func font(_ size: CGFloat, bold: Bool = false, mono: Bool = false) -> NSFont {
        NSFont(name: mono ? "Menlo-Regular" : (bold ? boldName : regularName), size: size)!
    }
    func attr(_ text: String, size: CGFloat, bold: Bool = false, white: Bool = false) -> NSAttributedString {
        let p = NSMutableParagraphStyle(); p.lineSpacing = 3.8; p.lineBreakMode = .byWordWrapping
        let base: [NSAttributedString.Key: Any] = [.font: font(size, bold: bold), .foregroundColor: NSColor(cgColor: white ? color(0xFFFFFF) : ink)!, .paragraphStyle: p]
        let out = NSMutableAttributedString(string: "")
        for (i, part) in text.replacingOccurrences(of: "**", with: "").components(separatedBy: "`").enumerated() {
            var a = base
            if i % 2 == 1 { a[.font] = font(max(10.4,size-0.7), mono: true) }
            out.append(NSAttributedString(string: part, attributes: a))
        }
        return out
    }
    func height(_ a: NSAttributedString, width: CGFloat) -> CGFloat {
        let fs = CTFramesetterCreateWithAttributedString(a)
        let size = CTFramesetterSuggestFrameSizeWithConstraints(fs, CFRange(location: 0,length: 0), nil,
                                                               CGSize(width: width,height: 2000), nil)
        return ceil(size.height)+3
    }
    func text(_ string: String, x: CGFloat = margin, y: CGFloat, width: CGFloat = bodyWidth,
              size: CGFloat = 12.2, bold: Bool = false, white: Bool = false, record: Bool = false) -> CGFloat {
        let a = attr(string, size: size, bold: bold, white: white)
        let h = height(a, width: width)
        let path = CGPath(rect: CGRect(x: x,y: pageHeight-y-h,width: width,height: h), transform: nil)
        let frame = CTFramesetterCreateFrame(CTFramesetterCreateWithAttributedString(a), CFRange(location: 0,length: 0), path, nil)
        context.saveGState(); context.textMatrix = .identity; CTFrameDraw(frame,context); context.restoreGState()
        if record { boxes.append(TextBox(page: page,top: Double(y),height: Double(h),width: Double(width),text: string)) }
        return h
    }
    func rect(_ x: CGFloat,_ y: CGFloat,_ w: CGFloat,_ h: CGFloat,_ fill: CGColor, radius: CGFloat = 0, stroke: CGColor? = nil) {
        let r = CGRect(x: x,y: pageHeight-y-h,width: w,height: h)
        context.saveGState(); context.addPath(CGPath(roundedRect: r,cornerWidth: radius,cornerHeight: radius,transform: nil))
        context.setFillColor(fill)
        if let stroke = stroke { context.setStrokeColor(stroke); context.setLineWidth(0.7); context.drawPath(using: .fillStroke) }
        else { context.fillPath() }
        context.restoreGState()
    }
    func line(_ x: CGFloat,_ y: CGFloat,_ x2: CGFloat,_ y2: CGFloat,_ stroke: CGColor,_ width: CGFloat = 1) {
        context.saveGState(); context.setStrokeColor(stroke); context.setLineWidth(width)
        context.move(to: CGPoint(x: x,y: pageHeight-y)); context.addLine(to: CGPoint(x: x2,y: pageHeight-y2)); context.strokePath(); context.restoreGState()
    }
    func ensure(_ h: CGFloat) throws {
        if top+h > pageHeight-60 { throw BuildError.message("Page \(page) overflow at top \(top), block \(h); edit Markdown page breaks, do not shrink body type.") }
    }
    func paragraph(_ s: String, bullet: Bool = false) throws {
        let x = margin + (bullet ? 14 : 0), width = bodyWidth - (bullet ? 14 : 0)
        let h = height(attr(s,size: 12.2),width: width); try ensure(h+7)
        if bullet { rect(margin+1,top+7,4,4,accent,radius: 1) }
        _ = text(s,x: x,y: top,width: width,record: true); top += h+7
    }
    func heading(_ s: String, level: Int) throws {
        if level == 1 {
            let size: CGFloat = page == 1 ? 29 : 25
            let h = height(attr(s,size: size,bold: true),width: bodyWidth); try ensure(h+21)
            rect(margin,top-6,34,3,accent,radius: 1); _ = text(s,y: top,width: bodyWidth,size: size,bold: true,record: true); top += h+19
        } else {
            top += 7; let h = height(attr(s,size: 15,bold: true),width: bodyWidth); try ensure(h+10)
            _ = text(s,y: top,width: bodyWidth,size: 15,bold: true,record: true); top += h+7
        }
    }
    func note(_ s: String) throws {
        let h = height(attr(s,size: 11.6),width: bodyWidth-28)+20; try ensure(h+10)
        rect(margin,top,bodyWidth,h,pale,radius: 5); rect(margin,top,3,h,accent,radius: 1)
        _ = text(s,x: margin+14,y: top+9,width: bodyWidth-28,size: 11.6,record: true); top += h+10
    }
    func table(_ rows: [[String]]) throws {
        let first = rows[0][0].contains("脚本") ? bodyWidth*0.46 : bodyWidth*0.38
        let widths = [first,bodyWidth-first]
        for (i,row) in rows.enumerated() {
            let heights = row.enumerated().map { j,s in height(attr(s,size: 10.8,bold: i==0),width: widths[j]-18) }
            let h = max(heights.max() ?? 20,21)+14; try ensure(h)
            var x = margin
            for j in 0..<2 {
                rect(x,top,widths[j],h,i==0 ? color(0x244356) : (i%2==0 ? color(0xF2F6F8) : color(0xFFFFFF)),stroke: color(0xD6DFE5))
                _ = text(row[j],x: x+9,y: top+7,width: widths[j]-18,size: 10.8,bold: i==0,white: i==0,record: true); x += widths[j]
            }
            top += h
        }
        top += 13
    }
    func button(_ s: String,_ x: CGFloat,_ y: CGFloat,_ w: CGFloat,_ h: CGFloat = 26) {
        rect(x,y,w,h,color(0xFFFFFF),radius: 4,stroke: color(0x8FA5B5))
        _ = text(s,x: x+9,y: y+4,width: w-18,size: 10.2,bold: true)
    }
    func tent(_ x: CGFloat,_ y: CGFloat,_ w: CGFloat,_ h: CGFloat) {
        rect(x,y,w,h,color(0xDFEFD6),radius: 3)
        context.saveGState(); context.setFillColor(color(0xD96D34));context.move(to: CGPoint(x:x+w*0.25,y:pageHeight-y-h*0.79));context.addLine(to:CGPoint(x:x+w*0.52,y:pageHeight-y-h*0.19));context.addLine(to:CGPoint(x:x+w*0.80,y:pageHeight-y-h*0.79));context.closePath();context.fillPath();context.restoreGState()
        line(x+w*0.52,y+h*0.2,x+w*0.52,y+h*0.79,color(0x743C21),1.2)
    }
    func figure(_ kind: String) throws {
        let h: CGFloat = kind == "START_FLOW" ? 84 : (kind == "PHOTO_GROUPS" ? 214 : (kind == "VIDEO_SEARCH" ? 210 : 220))
        try ensure(h+22); let y=top
        rect(margin,y,bodyWidth,h,color(0xF5F8FA),radius: 7,stroke: color(0xBDCBD6))
        if kind == "START_FLOW" {
            let cells=["Finder 找到入口","关闭旧窗口","Stop 后等待","双击 Open"]
            let w: CGFloat=108
            for (i,s) in cells.enumerated() { let x=margin+10+CGFloat(i)*126;rect(x,y+19,w,34,color(0xFFFFFF),radius: 5,stroke: color(0xB5C5D1));_ = text(s,x:x+8,y:y+26,width:w-16,size:10.3,bold:true);if i<3 {_ = text("→",x:x+w+4,y:y+25,width:15,size:12,bold:true)} }
            _ = text("已有索引继续使用",x:margin+12,y:y+60,width:bodyWidth-24,size:10.2)
        } else if kind == "PHOTO_GROUPS" {
            button("恢复原始排序",margin+14,y+14,124)
            _ = text("核验支持 · 1 个（可能出错）",x:margin+14,y:y+49,width:bodyWidth-28,size:12,bold:true)
            tent(margin+14,y+77,130,65)
            _ = text("演示照片 01",x:margin+160,y:y+81,width:250,size:11)
            button("查看原图",margin+160,y+106,90)
            button("展开其他原始候选 · 2 个（未确认或未获支持）",margin+14,y+160,bodyWidth-28,30)
        } else {
            button(kind == "VIDEO_SEARCH" ? "鸭子" : "帐篷",margin+14,y+13,242)
            button(kind == "VIDEO_SEARCH" ? "视频内部画面 ▾" : "照片 ▾",margin+266,y+13,137)
            button("查找",margin+414,y+13,74)
            if kind == "VIDEO_SEARCH" {
                _ = text("1 · 演示视频（合成示意）",x:margin+14,y:y+52,width:bodyWidth-28,size:11)
                button("播放视频",margin+14,y+79,92)
                rect(margin+121,y+79,bodyWidth-135,104,color(0xD8EAF4),radius:4)
                rect(margin+121,y+144,bodyWidth-135,39,color(0x92B2A4))
                line(margin+136,y+199,margin+468,y+199,color(0x8498A8),3)
                rect(margin+242,y+194,9,11,accent,radius:4)
                _ = text("拖动播放器进度条",x:margin+145,y:y+162,width:250,size:10.4)
            } else {
                _ = text("原始候选已显示；正在本机核验照片。",x:margin+14,y:y+53,width:bodyWidth-28,size:11)
                tent(margin+14,y+85,130,82)
                _ = text("演示照片 01",x:margin+160,y:y+93,width:250,size:11)
                button("查看原图",margin+160,y+121,90)
                _ = text("原始候选先出现，核验随后完成。",x:margin+14,y:y+185,width:bodyWidth-28,size:10.5)
            }
        }
        _ = text("合成操作示意 · 不是图库截图",x:margin+4,y:y+h+3,width:bodyWidth-8,size:9.5)
        top += h+29
    }
    func begin(_ number: Int,_ total: Int) {
        page=number;top=54;context.beginPDFPage(nil);rect(0,0,pageWidth,pageHeight,color(0xFFFFFF))
        _ = text("MEMORYSEARCH  /  本机操作指南",y:23,size:9.3,bold:true)
        line(margin,pageHeight-48,pageWidth-margin,pageHeight-48,color(0xD8E1E8),0.6)
        _ = text("现有索引版 · 2026-11-25",y:pageHeight-36,width:320,size:9.2)
        _ = text("第 \(number) / \(total) 页",x:pageWidth-margin-80,y:pageHeight-36,width:80,size:9.2)
    }
}

final class FontCollector {
    var entries=[[String:Any]]()
    func font(_ d: CGPDFDictionaryRef, resource: String) {
        var base: UnsafePointer<CChar>?, subtype: UnsafePointer<CChar>?, descriptor: CGPDFDictionaryRef?
        CGPDFDictionaryGetName(d,"BaseFont",&base);CGPDFDictionaryGetName(d,"Subtype",&subtype)
        var descendants: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(d,"DescendantFonts",&descendants),let a=descendants {
            for i in 0..<CGPDFArrayGetCount(a) {var child:CGPDFDictionaryRef?;if CGPDFArrayGetDictionary(a,i,&child),let child=child {font(child,resource:resource+"/CID")}}
            return
        }
        var embedded=false
        if CGPDFDictionaryGetDictionary(d,"FontDescriptor",&descriptor),let fd=descriptor {
            for key in ["FontFile","FontFile2","FontFile3"] {var stream:CGPDFStreamRef?;if CGPDFDictionaryGetStream(fd,key,&stream),stream != nil {embedded=true}}
        }
        entries.append(["resource":resource,"font":base.map{String(cString:$0)} ?? "unknown","subtype":subtype.map{String(cString:$0)} ?? "unknown","embedded":embedded])
    }
}
func fontCallback(_ key: UnsafePointer<CChar>,_ value: CGPDFObjectRef,_ info: UnsafeMutableRawPointer?) {
    guard let info=info else{return};let collector=Unmanaged<FontCollector>.fromOpaque(info).takeUnretainedValue()
    var d:CGPDFDictionaryRef?;if CGPDFObjectGetValue(value,.dictionary,&d),let d=d {collector.font(d,resource:String(cString:key))}
}

do {
    try fm.createDirectory(at: outputURL.deletingLastPathComponent(),withIntermediateDirectories:true)
    try fm.createDirectory(at: qaURL,withIntermediateDirectories:true)
    let source = try String(contentsOf: sourceURL,encoding:.utf8)
    let pages = source.components(separatedBy:"<!-- PAGE -->").map{$0.trimmingCharacters(in:.whitespacesAndNewlines)}.filter{!$0.isEmpty}
    guard pages.count==7 else {throw BuildError.message("Expected 7 deliberately paginated SOP pages")}
    let buildingURL=outputURL.appendingPathExtension("building")
    var media=CGRect(x:0,y:0,width:pageWidth,height:pageHeight)
    let info:[CFString:Any]=[kCGPDFContextTitle:"MemorySearch 本机使用 SOP",kCGPDFContextAuthor:"MemorySearch",kCGPDFContextSubject:"中文操作指南；仅合成示意，无私人数据",kCGPDFContextCreator:"MemorySearch SOP / macOS CoreText"]
    guard let context=CGContext(buildingURL as CFURL,mediaBox:&media,info as CFDictionary) else{throw BuildError.message("Cannot create PDF context")}
    let p=Painter(context)
    for (i,page) in pages.enumerated() {
        p.begin(i+1,pages.count)
        let lines=page.components(separatedBy:.newlines);var n=0
        while n<lines.count {
            let line=lines[n].trimmingCharacters(in:.whitespaces)
            if line.isEmpty {n+=1;continue}
            if line.hasPrefix("# ") {try p.heading(String(line.dropFirst(2)),level:1);n+=1;continue}
            if line.hasPrefix("## ") {try p.heading(String(line.dropFirst(3)),level:2);n+=1;continue}
            if line.hasPrefix("{{"),line.hasSuffix("}}") {try p.figure(String(line.dropFirst(2).dropLast(2)));n+=1;continue}
            if line.hasPrefix("> ") {try p.note(String(line.dropFirst(2)));n+=1;continue}
            if line.hasPrefix("|") {
                var table=[[String]]()
                while n<lines.count,lines[n].trimmingCharacters(in:.whitespaces).hasPrefix("|") {
                    let cells=lines[n].split(separator:"|",omittingEmptySubsequences:true).map{$0.trimmingCharacters(in:.whitespaces)}
                    if !cells.allSatisfy({$0.allSatisfy{$0=="-" || $0==":" || $0==" "}}) {guard cells.count==2 else{throw BuildError.message("Only two-column tables supported")};table.append(cells)}
                    n+=1
                }
                try p.table(table);continue
            }
            if line.hasPrefix("- ") {try p.paragraph(String(line.dropFirst(2)),bullet:true);n+=1;continue}
            try p.paragraph(line);n+=1
        }
        context.endPDFPage()
    }
    context.closePDF()
    // Public documentation must not retain the builder host's OS build metadata.
    guard let neutral=PDFDocument(url:buildingURL) else{throw BuildError.message("Cannot inspect PDF metadata")}
    neutral.documentAttributes = [
        PDFDocumentAttribute.titleAttribute: "MemorySearch 本机使用 SOP",
        PDFDocumentAttribute.authorAttribute: "MemorySearch",
        PDFDocumentAttribute.subjectAttribute: "中文操作指南；仅合成示意，无私人数据",
        PDFDocumentAttribute.creatorAttribute: "MemorySearch SOP / macOS CoreText",
        PDFDocumentAttribute.producerAttribute: "MemorySearch documentation builder"
    ]
    guard neutral.write(to:buildingURL) else{throw BuildError.message("Cannot normalize public PDF metadata")}
    guard let pdf=PDFDocument(url:buildingURL),pdf.pageCount==7 else{throw BuildError.message("PDF page count check failed")}
    var extracted="",pageReports=[[String:Any]]();let fonts=FontCollector()
    for i in 0..<pdf.pageCount {
        let page=pdf.page(at:i)!;let text=page.string ?? "";guard text.count>100 else{throw BuildError.message("Text extraction failed on page \(i+1)")}
        extracted += "\n--- PAGE \(i+1) ---\n"+text
        let b=page.bounds(for:.mediaBox);let scale:CGFloat=2;let width=Int(ceil(b.width*scale)),height=Int(ceil(b.height*scale))
        guard let bitmap=CGContext(data:nil,width:width,height:height,bitsPerComponent:8,bytesPerRow:width*4,space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGImageAlphaInfo.premultipliedLast.rawValue) else{throw BuildError.message("Bitmap allocation failed")}
        bitmap.setFillColor(color(0xFFFFFF));bitmap.fill(CGRect(x:0,y:0,width:width,height:height));bitmap.scaleBy(x:scale,y:scale);page.draw(with:.mediaBox,to:bitmap)
        let image=bitmap.makeImage()!;let png=NSBitmapImageRep(cgImage:image).representation(using:.png,properties:[:])!
        try png.write(to:qaURL.appendingPathComponent(String(format:"page-%02d.png",i+1)))
        if let ref=page.pageRef, let dictionary=ref.dictionary {var resources:CGPDFDictionaryRef?,fontDict:CGPDFDictionaryRef?;if CGPDFDictionaryGetDictionary(dictionary,"Resources",&resources),let resources=resources,CGPDFDictionaryGetDictionary(resources,"Font",&fontDict),let fontDict=fontDict {CGPDFDictionaryApplyFunction(fontDict,fontCallback,Unmanaged.passUnretained(fonts).toOpaque())}}
        pageReports.append(["page":i+1,"text_characters":text.count,"width_points":b.width,"height_points":b.height,"PNG_width":width,"PNG_height":height])
    }
    let compact=extracted.replacingOccurrences(of:"\\s",with:"",options:.regularExpression)
    for needle in ["Open-Local-Memory.command","Stop-Local-Memory.command","Rollback-Photo-Review.command","恢复原始排序","查看核验分组","视频内部画面","播放视频","无需重新建索引"] {guard compact.contains(needle) else{throw BuildError.message("Extracted PDF missing: \(needle)")}}
    guard !extracted.contains("/Users/"),!extracted.contains("PRIVATE_MEDIA"),!extracted.contains("file://") else{throw BuildError.message("Unexpected private/system path in PDF text")}
    guard !fonts.entries.isEmpty,fonts.entries.allSatisfy({$0["embedded"] as? Bool == true}) else{throw BuildError.message("PDF font embedding check failed: \(fonts.entries)")}
    try extracted.write(to:qaURL.appendingPathComponent("extracted.txt"),atomically:true,encoding:.utf8)
    let boxData=try JSONEncoder().encode(p.boxes);try boxData.write(to:qaURL.appendingPathComponent("layout-boxes.json"))
    let report:[String:Any]=["pages":pageReports,"font_resources":fonts.entries,"all_fonts_embedded":true,"layout_overflow":false,"required_text_checks":8,"contains_private_paths":false,"render_scale":2,"visual_review":"pending human/assistant inspection of every PNG"]
    try JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]).write(to:qaURL.appendingPathComponent("qa.json"))
    if fm.fileExists(atPath:outputURL.path) {try fm.removeItem(at:outputURL)}
    try fm.moveItem(at:buildingURL,to:outputURL)
    print(String(data:try JSONSerialization.data(withJSONObject:["PDF_pages":7,"all_fonts_embedded":true,"PNG_pages":7,"text_checks":8,"overflow":false],options:.sortedKeys),encoding:.utf8)!)
} catch { fputs("SOP build failed: \(error)\n",stderr);exit(1) }
