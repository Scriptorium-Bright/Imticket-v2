package org.example.ticket.seat.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.performance.domain.PerformanceTime;
import org.example.ticket.venue.domain.SeatGrade;

@Entity
@Table(name = "seat",
        indexes = @Index(name = "idx_seat_performance_status", columnList = "performance_time_id, seat_status"),
        uniqueConstraints = @UniqueConstraint(name = "uk_seat_performance_position",
                columnNames = {"performance_time_id", "seat_section", "seat_row", "seat_number"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class Seat {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "performance_time_id", nullable = false)
    private PerformanceTime performanceTime;
    @Column(name = "seat_section", nullable = false, length = 20)
    private String section;
    @Column(name = "seat_row", nullable = false)
    private Integer rowNumber;
    @Column(name = "seat_number", nullable = false)
    private Integer seatNumber;
    @Enumerated(EnumType.STRING)
    @Column(name = "seat_grade", nullable = false, length = 10)
    private SeatGrade grade;
    @Column(nullable = false)
    private Integer price;
    @Enumerated(EnumType.STRING)
    @Column(name = "seat_status", nullable = false, length = 20)
    private SeatStatus status;
    @Version
    private Long version;

    public void lock() { status = SeatStatus.LOCKED; }
    public void reserve() { status = SeatStatus.RESERVED; }
    public void release() { status = SeatStatus.AVAILABLE; }
}
