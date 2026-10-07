package org.example.ticket.performance.domain;

import jakarta.persistence.*;
import lombok.*;
import org.example.ticket.venue.domain.SeatGrade;

@Entity
@Table(name = "seat_price",
        uniqueConstraints = @UniqueConstraint(name = "uk_performance_seat_grade",
                columnNames = {"performance_id", "seat_grade"}))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class SeatPrice {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "performance_id", nullable = false)
    private Performance performance;
    @Enumerated(EnumType.STRING)
    @Column(name = "seat_grade", nullable = false, length = 10)
    private SeatGrade grade;
    @Column(nullable = false)
    private Integer price;
}
